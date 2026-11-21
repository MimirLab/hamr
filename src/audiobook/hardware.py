"""Read-only host observations and advisory backend choices; never activate a model."""
from __future__ import annotations

import csv
import ctypes.util
import io
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import json
from .runtime_catalog import runtime_key

GIB = 1024 ** 3
UPSTREAM = 'https://github.com/HoppouAI/Breeze-TTS-2.cpp/blob/4110a9fff4e47d4d1ba850d92d81f3ba86bf39c6/README.md#weights'
# Conservative working ranges, not measurements or hard admission limits.
# GPU lower bounds follow the pinned upstream table. CPU offload is unmeasured.
BUDGETS = {
    'vulkan-f16': ((7, 9), (4, 8), 'F16', 'Least weight compression; reference baseline.', 'Local speech/clone rendering on Radeon RX 6800 XT 16 GiB.'),
    'vulkan-q8': ((4, 6), (3, 6), 'Q8', 'Smaller weights and lower memory use. Compresses parts of the depth decoder and codec.', 'Local matched speech/clone controls on Radeon RX 6800 XT 16 GiB.'),
    'vulkan-q6': ((3.5, 5), (3, 6), 'Q6', 'More compression; speed and quality need local comparison.', None),
    'vulkan-q4': ((3, 4.5), (3, 6), 'Q4', 'Smallest implemented GPU model; greater compression tradeoff.', None),
    'vulkan-q8-cpu-text-encoder': ((4, 6), (8, 12), 'Q8', 'Same Q8 weights; CPU text encoding can reduce GPU pressure and slow generation. Budget conservatively until measured.', None),
    'cpu-f16': ((0, 0), (10, 16), 'F16', 'F16 weights on the CPU; highest memory budget, potentially very slow.', None),
    'cpu-q8': ((0, 0), (8, 12), 'Q8', 'Q8 weights on the CPU; trades memory and time for less compression.', None),
    'cpu-q6': ((0, 0), (7, 11), 'Q6', 'Q6 weights on the CPU; intermediate memory/precision choice.', None),
    'cpu-q4': ((0, 0), (6, 10), 'Q4', 'No GPU work; CPU generation may be much slower than playback.', None),
}


def _command(args):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=5, check=False)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return None, '', type(exc).__name__


def _read(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ''


def _integer(path):
    try:
        return int(_read(path))
    except ValueError:
        return None


def inspect_host(sysroot=Path('/sys'), procroot=Path('/proc'), command=_command):
    """No model/download; optional bounded vendor and Vulkan information tools."""
    system = platform.system()
    linux = system == 'Linux'
    meminfo = _read(procroot / 'meminfo') if linux else ''
    memory = {k: int(v) * 1024 for k, v in re.findall(r'^(MemTotal|MemAvailable):\s+(\d+)', meminfo, re.M)}
    cpu = _read(procroot / 'cpuinfo') if linux else ''
    flags = re.search(r'^flags\s*:\s*(.*)$', cpu, re.M)
    pci = {}
    if linux and shutil.which('lspci'):
        code, out, _ = command(['lspci', '-D', '-nn'])
        if code == 0:
            for row in out.splitlines():
                if re.search(r'VGA|3D controller|Display controller', row, re.I):
                    bus, _, description = row.partition(' ')
                    pci[bus] = description
    gpus = []
    seen = set()
    cards = sorted((sysroot / 'class/drm').glob('card[0-9]*')) if linux else []
    for card in cards:
        if not re.fullmatch(r'card\d+', card.name) or not (card / 'device').exists():
            continue
        device = (card / 'device').resolve()
        if str(device) in seen:
            continue
        seen.add(str(device))
        vendor = {'0x1002': 'AMD', '0x10de': 'NVIDIA', '0x8086': 'Intel'}.get(_read(device / 'vendor'), 'Unknown')
        driver = (device / 'driver').resolve().name if (device / 'driver').exists() else None
        gpus.append({'device': str(device), 'pci_bus': device.name, 'vendor': vendor,
                     'driver': driver, 'pci_id': _read(device / 'device'),
                     'name': pci.get(device.name, f'{vendor} GPU {_read(device / "device")}').strip(),
                     'vram_bytes': _integer(device / 'mem_info_vram_total'),
                     'used_bytes': _integer(device / 'mem_info_vram_used')})
    if shutil.which('nvidia-smi'):
        code, out, _ = command(['nvidia-smi', '--query-gpu=pci.bus_id,name,memory.total,memory.free', '--format=csv,noheader,nounits'])
        if code == 0:
            for row in csv.reader(io.StringIO(out)):
                if len(row) != 4:
                    continue
                bus, name, total, free = [v.strip() for v in row]
                bus = re.sub(r'^00000000:', '0000:', bus).lower()
                try:
                    total, free = int(total) * 1024**2, int(free) * 1024**2
                except ValueError:
                    continue
                gpu = next((g for g in gpus if g['pci_bus'].lower() == bus), None)
                if gpu is None:
                    gpu = {'device': bus, 'pci_bus': bus, 'driver': 'nvidia', 'pci_id': ''}
                    gpus.append(gpu)
                gpu.update(name=name, vendor='NVIDIA', vram_bytes=total, used_bytes=max(0, total-free))
    for gpu in gpus:
        total, used = gpu.get('vram_bytes'), gpu.get('used_bytes')
        gpu['available_vram_bytes'] = max(0, total-used) if total is not None and used is not None else None
        gpu['memory_kind'] = 'dedicated' if total else 'unknown_or_shared'
    vulkan = {'status': 'unknown', 'loader_found': bool(ctypes.util.find_library('vulkan')), 'devices': [],
              'reason': 'vulkaninfo is not installed; GPU/driver presence alone does not prove usable Vulkan. Install vulkan-tools and run vulkaninfo --summary.'}
    if shutil.which('vulkaninfo'):
        code, out, err = command(['vulkaninfo', '--summary'])
        devices = []
        for block in re.split(r'GPU\d+\s*:', out)[1:]:
            name = re.search(r'deviceName\s*=\s*(.+)', block)
            kind = re.search(r'deviceType\s*=\s*(.+)', block)
            if name:
                devices.append({'name': name[1].strip(), 'type': kind[1].strip() if kind else 'unknown'})
        real = [d for d in devices if 'CPU' not in d['type'].upper() and not re.search(r'llvmpipe|lavapipe|software', d['name'], re.I)]
        if code == 0 and real:
            vulkan.update(status='available', devices=devices, reason='vulkaninfo enumerated a hardware Vulkan device; model allocation and speech are not tested.')
            if not gpus:
                gpus = [{'name': d['name'], 'vendor': 'Unknown', 'vram_bytes': None, 'available_vram_bytes': None, 'memory_kind': 'unknown_or_shared'} for d in real]
        elif code == 0 and devices:
            vulkan.update(status='unavailable', devices=devices, reason='Only a software/CPU Vulkan device was enumerated.')
        elif code is None:
            vulkan.update(reason='vulkaninfo could not complete within its bounded probe; Vulkan support remains unknown.')
        else:
            vulkan.update(status='unavailable', reason='vulkaninfo failed to enumerate hardware devices. Check the Vulkan loader, vendor driver/ICD and device access.', diagnostic=(err or out)[-400:])
    if system in ('Linux', 'Windows'):
        code, out, err = command([sys.executable, '-m', 'audiobook.vulkan_probe'])
        try:
            probe = json.loads(out) if code == 0 else None
        except ValueError:
            probe = None
        if isinstance(probe, dict) and probe.get('status') in ('available', 'unavailable'):
            vulkan.update(probe, probe='built-in Vulkan inventory')
            if probe['status'] == 'available':
                vulkan['loader_found'] = True
            for index, device in enumerate(probe.get('devices', [])):
                if device.get('type') not in ('INTEGRATED_GPU', 'DISCRETE_GPU', 'VIRTUAL_GPU'):
                    continue
                gpu = next((g for g in gpus if not g.get('vulkan_device') and g.get('pci_id', '').lower() == f'0x{device.get("device_id", -1):04x}'), None)
                if gpu is None:
                    gpu = next((g for g in gpus if not g.get('vulkan_device') and not g.get('pci_id') and g['name'].casefold() == device['name'].casefold()), None)
                if gpu is None:
                    gpu = {'device': f'vulkan:{index}', 'vendor': {0x1002:'AMD',0x10de:'NVIDIA',0x8086:'Intel'}.get(device.get('vendor_id'), 'Unknown'), 'driver': 'Vulkan', 'pci_id': f'0x{device.get("device_id", 0):04x}'}
                    gpus.append(gpu)
                gpu.update(name=device['name'], vulkan_device=True)
                gpu['memory_kind'] = 'shared' if device['type'] == 'INTEGRATED_GPU' else 'dedicated'
                if not gpu.get('vram_bytes'):
                    gpu['vram_bytes'] = device.get('vram_bytes')
                if gpu.get('available_vram_bytes') is None and device.get('available_budget_bytes') is not None:
                    gpu['available_vram_bytes'] = device['available_budget_bytes']
                    gpu['memory_measurement'] = 'driver_budget'
        elif vulkan['status'] != 'available':
            vulkan.update(status='unknown', reason='The built-in Vulkan inventory did not complete. Run vulkaninfo --summary to inspect the driver; no model was loaded.', diagnostic=err[-240:])
    total, available = memory.get('MemTotal'), memory.get('MemAvailable')
    avx2 = 'avx2' in flags[1].split() if flags else None
    if system == 'Windows':
        from .platform_support import windows_memory
        total, available, avx2 = windows_memory()
    return {'system': system, 'arch': platform.machine(), 'gpus': gpus,
            'ram_total_bytes': total, 'ram_available_bytes': available, 'cpu_avx2': avx2, 'vulkan': vulkan}


def profile_catalog(catalog, host=None):
    runtime_variants = catalog['runtimes']['hoppou']
    variant = runtime_key(host['system'],host['arch']) if host else runtime_key()
    runtime = runtime_variants.get(variant, runtime_variants['linux_x86_64'])
    recipes = json.loads(Path(__file__).with_name('gguf_recipes.json').read_text(encoding='utf-8'))
    license_bytes = next(f['bytes'] for f in catalog['packages']['official']['files'] if f['path'] == 'LICENSE')
    result = []
    for entry in catalog['profiles']:
        row = {**entry, 'implemented': entry['runtime'] == 'hoppou' and entry['id'] in BUDGETS}
        if row['implemented']:
            vram, ram, precision, tradeoff, tested = BUDGETS[row['id']]
            package = entry['packages'][0]
            recipe = recipes.get(package)
            if recipe and recipe['model_sha256'] != catalog['packages'][package]['files'][0]['sha256']:
                recipe = None
            status = 'locally_tested' if tested else 'implemented_experimental'
            if variant == 'windows_x86_64':
                status, tested = 'implemented_experimental', None
            row.update(platforms=['Linux x86_64 with AVX2','Windows x86_64 with AVX2 (preview)'], precision=precision,
                       estimated_peak_vram_gib=list(vram), estimated_peak_system_ram_gib=list(ram),
                       memory_basis='Planning estimates; upstream approximate GPU footprint plus transient headroom. System RAM/offload peaks unmeasured.',
                       download_bytes=runtime['bytes'] + sum(catalog['packages'][p]['payload_bytes'] for p in entry['packages']) + license_bytes,
                       tradeoff=tradeoff, testing_status=status,
                       testing_evidence=tested, memory_source=UPSTREAM, tensor_recipe=recipe)
        else:
            row.update(testing_status='catalog_only', availability='No setup adapter in this release.')
        result.append(row)
    return result


def advise(host, catalog):
    profiles = profile_catalog(catalog,host)
    warnings = []
    supported = runtime_key(host['system'],host['arch']) in catalog['runtimes']['hoppou']
    candidates = host['gpus']
    if host['vulkan']['status'] == 'available' and any(g.get('vulkan_device') for g in candidates):
        candidates = [g for g in candidates if g.get('vulkan_device')]
    gpu = max(candidates, key=lambda g: g.get('available_vram_bytes') or 0, default=None)
    free = gpu.get('available_vram_bytes') if gpu else None
    ram = host.get('ram_available_bytes')
    if len(host['gpus']) > 1:
        warnings.append('Multiple GPUs detected. Advice assumes the GPU with most observed free VRAM; the backend device choice must be checked separately.')
    if host['cpu_avx2'] is None:
        warnings.append('CPU AVX2 capability could not be determined; the prebuilt runtime requires it.')
    if host['vulkan']['status'] == 'unknown':
        warnings.append(host['vulkan']['reason'])
    if gpu and free is None:
        warnings.append('Free dedicated VRAM is unknown or shared with system RAM; memory fit cannot be established.')
    for row in profiles:
        if not row['implemented']:
            continue
        reasons = []
        status = 'likely'
        needs_gpu = row['settings'].get('device') != 'cpu'
        if not supported:
            status = 'unsupported'; reasons.append('The packaged speech engine needs x86_64 Linux or Windows. Other engine families require their own adapter.')
        elif host['cpu_avx2'] is False:
            status = 'unsupported'; reasons.append('The prebuilt runtime requires an AVX2 CPU, including for GPU profiles.')
        elif host['vulkan'].get('loader_found') is False:
            loader = 'vulkan-1.dll from a current graphics driver' if host['system']=='Windows' else 'libvulkan.so.1'
            status = 'unavailable'; reasons.append(f'Install the Vulkan loader library ({loader}). The shipped runtime links it even for CPU execution.')
        elif needs_gpu and host['vulkan']['status'] == 'unavailable':
            status = 'unavailable'; reasons.append(host['vulkan']['reason'])
        else:
            if host['cpu_avx2'] is None:
                status = 'tentative'; reasons.append('AVX2 is unverified.')
            if needs_gpu:
                if host['vulkan']['status'] != 'available':
                    status = 'tentative'; reasons.append('Hardware Vulkan support is unverified.')
                if free is None:
                    status = 'tentative'; reasons.append('GPU memory fit is unknown.')
                else:
                    low, high = row['estimated_peak_vram_gib']
                    label = 'driver memory budget' if gpu.get('memory_measurement') == 'driver_budget' else 'observed free VRAM'
                    reasons.append(f'{free/GIB:.1f} GiB {label}; estimated working range {low:g}–{high:g} GiB.')
                    if free < low*GIB:
                        status = 'insufficient_memory'
                    elif free < high*GIB:
                        status = 'tentative'; reasons.append('Only the lower end of the estimated range fits; transient headroom is tight.')
            low, high = row['estimated_peak_system_ram_gib']
            if ram is None:
                if status == 'likely': status = 'tentative'
                reasons.append('Available system RAM is unknown.')
            else:
                reasons.append(f'{ram/GIB:.1f} GiB system RAM available; estimated working range {low:g}–{high:g} GiB.')
                if ram < low*GIB:
                    status = 'insufficient_memory'
                elif ram < high*GIB and status == 'likely':
                    status = 'tentative'
            if not needs_gpu:
                reasons.append('CPU execution does not require a usable Vulkan GPU. The shipped binary still links the Vulkan loader.')
        row.update(compatibility=status, reasons=reasons)
    order = ['vulkan-f16', 'vulkan-q8', 'vulkan-q6', 'vulkan-q4', 'cpu-q4']
    by_id = {r['id']: r for r in profiles if r['implemented']}
    # Prefer headroom over an F16 model that only barely fits. Unknown probes
    # stay advisory; never activate settings based on these observations.
    gpu_rows = [by_id[i] for i in order[:-1] if gpu is not None and i in by_id and by_id[i]['compatibility'] in ('likely', 'tentative')]
    if free is None:
        gpu_rows.sort(key=lambda r: r['estimated_peak_vram_gib'][1])
    if gpu and gpu.get('memory_kind') == 'shared':
        gpu_rows.sort(key=lambda r: r['estimated_peak_vram_gib'][1])
        warnings.append('Integrated GPU memory is shared with system RAM. Starting with a smaller model leaves more room for the desktop and CPU work.')
        for row in gpu_rows:
            row['compatibility'] = 'tentative'
    roomy = [r for r in gpu_rows if free is not None and free >= r['estimated_peak_vram_gib'][1]*GIB]
    choice = next(iter(roomy), None) or min(gpu_rows, key=lambda r: r['estimated_peak_vram_gib'][1], default=None)
    if choice is None and 'cpu-q4' in by_id and by_id['cpu-q4']['compatibility'] in ('likely', 'tentative'):
        choice = by_id['cpu-q4']
    if choice:
        recommendation = {'profile': choice['id'], 'command': f'hamr setup --profile {choice["id"]}',
                          'confidence': 'tentative' if choice['compatibility'] != 'likely' or len(host['gpus']) > 1 else 'moderate',
                          'reasons': choice['reasons'], 'testing_status': choice['testing_status'],
                          'uncertainty': 'Fit estimate. Setup checks loading on this computer. Choosing the best sound requires a matched listening comparison.',
                          'quality_basis': 'prefer reference precision when headroom permits; quality equivalence has not been established',
                          'automatic_eligible': choice['compatibility'] == 'likely' and bool(choice['testing_evidence']) and len(host['gpus']) <= 1}
    else:
        recommendation = None
        warnings.append('No implemented profile has an established memory/platform fit. See profile reasons and the hardware guide.')
    return {'profiles': profiles, 'recommended_profile': recommendation, 'uncertainties': warnings}
