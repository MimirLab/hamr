"""Pinned Fluid CPU recognizer used for book verification and diagnostics."""
from dataclasses import dataclass
import json
import math
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from .backend import acquire
from .checks_setup import preflight
from .core import Error, filehash, key, loadjson, writejson

ASSETS = Path(__file__).with_name('asr_fluid_assets.json')
LOCK = ASSETS.with_name('asr-fluid.lock')
WORKER = ASSETS.with_name('asr_fluid_worker.py')
PROFILE = 'fluid-int8'
RECORD = 'speech-diagnostic.json'
RAM_HEADROOM = 1536 * 1024**2
BOOK_RAM_HEADROOM = 2816 * 1024**2
RUNTIME_DISK_BUDGET = 256 * 1024**2
RUNTIME_VERSIONS = {'onnx-asr':'0.12.0','onnxruntime':'1.32.0','numpy':'2.5.5'}


@dataclass(frozen=True)
class Recipe:
    python: str
    model: str

    def __str__(self):
        return PROFILE

    def identity(self):
        catalog = check_assets(Path(self.model))
        _, fingerprint = observer_identity()
        return {'schema_version':1,'profile':PROFILE,'fingerprint':fingerprint,
                'model_files':{item['path']:item['sha256'] for item in catalog['files']},
                'model_sha256':key([item['sha256'] for item in catalog['files']]),
                'device':'cpu','provider':'CPUExecutionProvider','compute_type':'int8-weights',
                'language':'auto','prompt':None,'vad_filter':False,'timing_kind':'unavailable'}


def memory_available():
    from .hardware import effective_ram, linux_memory

    return effective_ram(linux_memory().get('MemAvailable'),Path('/sys'),Path('/proc'))


def advice(store, host, *, book=False):
    catalog = loadjson(ASSETS)
    root = store.root/'assets'/'speech-diagnostic'
    model = root/('fluid-'+catalog['revision'][:12])
    download = sum(item['bytes'] for item in catalog['files'])
    missing = 0
    for item in catalog['files']:
        try:
            if (model/item['path']).stat().st_size!=item['bytes']:
                missing += item['bytes']
        except OSError:
            missing += item['bytes']
    runtime = root/('env-'+filehash(LOCK)[:16])/'bin/python'
    disk = missing + (0 if runtime.is_file() else RUNTIME_DISK_BUDGET)
    available = host.get('ram_available_bytes')
    free = shutil.disk_usage(store.root).free
    headroom = BOOK_RAM_HEADROOM if book else RAM_HEADROOM
    if host.get('system')!='Linux' or host.get('arch')!='x86_64':
        reason = 'The Fluid CPU recipe has been checked on Linux x86-64.'
    elif available is None or available<headroom:
        reason = f'Fluid needs {headroom/1024**3:g} GiB of available system RAM for '+('full takes.' if book else 'short excerpts.')
    elif free<disk:
        reason = 'There is insufficient free disk for the remaining Fluid weights and runtime.'
    else:
        reason = 'The Linux CPU recipe fits the observed RAM and disk. GPU memory is unused.'
    fit = (host.get('system')=='Linux' and host.get('arch')=='x86_64'
           and available is not None and available>=headroom and free>=disk)
    return {'recommended':PROFILE if fit else 'whisper','fits':fit,'reason':reason,
            'download_bytes':download,'additional_disk_budget_bytes':disk,'ram_headroom_bytes':headroom,
            'ram_available_bytes':available,'disk_available_bytes':free,
            'installed':(store.root/RECORD).is_file(),'coverage':'Full Hamr takes' if book else 'Local excerpts up to 30 seconds'}


def check_assets(model):
    catalog = loadjson(ASSETS)
    for item in catalog['files']:
        path = model / item['path']
        if (not path.is_file() or path.is_symlink() or path.stat().st_size!=item['bytes']
                or filehash(path)!=item['sha256']):
            raise Error('asr_diagnostic_integrity',f'Fluid diagnostic asset does not match: {path.name}.',
                        'Rerun hamr setup --diagnostic-asr fluid-int8.')
    return catalog


def observer_identity():
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise Error('missing_dependency','FFmpeg is needed for speech diagnostics.')
    identity = {'asset_manifest_sha256':filehash(ASSETS),'runtime_lock_sha256':filehash(LOCK),
                'worker_sha256':filehash(WORKER),'ffmpeg_sha256':filehash(Path(ffmpeg)),
                'runtime':RUNTIME_VERSIONS,'provider':'CPUExecutionProvider'}
    return ffmpeg,key(identity)


def observe_many(recipe, clips):
    available = memory_available()
    headroom = BOOK_RAM_HEADROOM if any(t['samples']>30*24000 for t in clips) else RAM_HEADROOM
    if available is None or available<headroom:
        raise Error('asr_diagnostic_memory',f'Fluid needs {headroom/1024**3:g} GiB of currently available system RAM.',
                    'Free system memory and resume. Use hamr setup --checks-only --asr-profile whisper for the lower-memory verifier.')
    ffmpeg, identity = observer_identity()
    model = Path(recipe.model)
    catalog = check_assets(model)
    request = {'model':str(model),'clips':clips,'ffmpeg':ffmpeg}
    try:
        result = subprocess.run([recipe.python,'-I',str(WORKER)],input=json.dumps(request).encode(),
                                stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,timeout=max(180,30*len(clips)))
        output = json.loads(result.stdout)
    except (OSError,subprocess.SubprocessError,ValueError) as exc:
        raise Error('asr_diagnostic_failed','Fluid could not decode the complete take on CPU.',
                    'Rerun hamr setup --checks-only and resume. Choose --asr-profile whisper if the Fluid runtime still fails.') from exc
    if (not isinstance(output,dict) or output.get('schema_version')!=1
            or output.get('provider')!='CPUExecutionProvider' or output.get('runtime')!=RUNTIME_VERSIONS
            or not isinstance(output.get('rows'),list) or len(output['rows'])!=len(clips)):
        raise Error('asr_contract','Fluid returned an unexpected input, runtime, or transcript.')
    for sent, row in zip(clips,output['rows']):
        if (not isinstance(row,dict) or row.get('pcm_sha256')!=sent['pcm_sha256']
                or row.get('processed_samples')!=sent['samples'] or not isinstance(row.get('text'),str)
                or not isinstance(row.get('seconds'),(int,float)) or not math.isfinite(row['seconds']) or row['seconds']<0):
            raise Error('asr_contract','Fluid returned an unexpected take identity or transcript.')
    check_assets(model)
    metadata = {name:value for name,value in output.items() if name!='rows'}
    return [{**metadata,**row,'status':'observed','profile':PROFILE,'model_revision':catalog['revision'],
             'observer_sha256':identity,'prompt':None,'vad':False,'cleanup':False,'timing_kind':'unavailable'}
            for row in output['rows']]


def observe(profile, pcm, receipt):
    recipe = Recipe(profile['python'],profile['model'])
    row, = observe_many(recipe,[{'pcm':str(pcm),'samples':receipt['samples'],'pcm_sha256':receipt['sha256']}])
    return row


def cached_observation(path, identity, receipt):
    if path.is_symlink():
        raise Error('unsafe_path','Fluid observation cannot be a symlink.')
    try:
        if path.stat().st_size>128*1024:
            return None
        raw = loadjson(path)
        row = raw['observation']
        if (raw['observation_sha256']==key(row) and row['observer_sha256']==identity
                and row['pcm_sha256']==receipt['sha256'] and row['processed_samples']==receipt['samples']
                and row.get('provider')=='CPUExecutionProvider' and isinstance(row.get('text'),str)):
            return row
    except (OSError,ValueError,KeyError,TypeError):
        return None
    return None


def screen(store, job, rows):
    """Check a bounded sample of disputed and apparently clean published takes."""
    from . import jobs, performance, qa
    from .speech_compare import comparison_tokens, notation_observation

    try:
        profile = configured(store)
        if profile is None:
            return None
        _, identity = observer_identity()
        check_assets(Path(profile['model']))
    except Error as exc:
        return {'status':'unavailable','profile':PROFILE,'reason':exc.code}
    units = {unit['id']:unit for unit in job['plan']['utterances']}
    short = [row for row in rows if row['audio_seconds']<=30]
    short.sort(key=lambda row:row['pcm_sha256'])
    disputed = [row for row in short if row['flags'] or row['word_errors'] or row.get('authored_sound_observation')]
    disputed_ids = {row['utterance'] for row in disputed}
    clean = [row for row in short if row['utterance'] not in disputed_ids]
    protected = [row for row in clean if row['comparison_quantities_expected'] or row['missing_parenthetical_counts']
                 or set(qa.tokens(row['expected']))&{"no","not","never","cannot"}]
    selected = []
    seen = set()
    for group,limit in ((disputed,2),(protected,1),(clean,1)):
        added = 0
        for row in group:
            sha = row['pcm_sha256']
            if sha in seen:
                continue
            selected.append(row)
            seen.add(sha)
            added += 1
            if added == limit:
                break
    result = {'status':'complete','profile':PROFILE,'selected':len(selected),'observed':0,'reused':0,'errors':[]}
    for row in selected:
        unit = units[row['utterance']]
        clip = jobs.clip_key(unit,job)
        receipt = jobs.valid_clip(store,clip)
        if not receipt or receipt['sha256']!=row['pcm_sha256']:
            raise Error('qa_clip_changed','Speech changed during diagnostics.')
        directory = store.root/'clips'/clip
        cache = directory/('fluid-'+key([identity,receipt['sha256']])+'.json')
        try:
            observation = cached_observation(cache,identity,receipt)
            if observation:
                result['reused'] += 1
            else:
                observation = observe(profile,directory/'audio.pcm',receipt)
                writejson(cache,{'observation':observation,'observation_sha256':key(observation)})
        except Error as exc:
            result['errors'].append({'utterance':row['utterance'],'code':exc.code})
            continue
        compared = notation_observation(unit['text'],performance.lexical_observation(unit,observation['text']))
        expected = comparison_tokens(unit['text'])
        actual = comparison_tokens(compared)
        primary = comparison_tokens(row['comparison_text'])
        row['challenger_observation'] = {**observation,'comparison_text':compared,
                                         'word_edits':qa.distance(expected,actual),'agrees_with_primary':actual==primary}
        result['observed'] += 1
    if result['errors']:
        result['status'] = 'partial' if result['observed'] else 'unavailable'
    return result


def configured(store):
    try:
        setup = loadjson(store.root/RECORD)
    except FileNotFoundError:
        return None
    except (OSError,ValueError) as exc:
        raise Error('asr_diagnostic_setup','Fluid diagnostic configuration is unreadable.') from exc
    if (not isinstance(setup,dict) or setup.get('profile')!=PROFILE or not setup.get('model')
            or not setup.get('python') or setup.get('lock_sha256')!=filehash(LOCK)):
        raise Error('asr_diagnostic_setup','Fluid diagnostic configuration needs to be refreshed.',
                    'Run hamr setup --diagnostic-asr fluid-int8.')
    return setup


def provision(store, progress, cache=None, *, activate=True):
    if sys.platform!='linux' or platform.machine()!='x86_64':
        raise Error('asr_diagnostic_platform','Fluid diagnostic setup is available for Linux x86-64.')
    preflight()
    catalog = loadjson(ASSETS)
    root = store.root/'assets'/'speech-diagnostic'
    root.mkdir(exist_ok=True)
    model = root/('fluid-'+catalog['revision'][:12])
    model.mkdir(exist_ok=True)
    lock_hash = filehash(LOCK)
    runtime = root/('env-'+lock_hash[:16])
    python = runtime/'bin/python'
    setup = {'schema_version':1,'profile':PROFILE,'python':str(python),'model':str(model),
             'lock_sha256':lock_hash,'license':catalog['license']}
    log = store.root/'runtime'/'fluid-diagnostic.log'
    try:
        progress.stage('Installing the Fluid speech diagnostic' if activate else 'Installing the Fluid speech verifier')
        with log.open('w') as output:
            version = sys.executable if sys.version_info[:2]==(3,12) else '3.12'
            subprocess.run(['uv','venv','--allow-existing','--python',version,runtime],stdin=subprocess.DEVNULL,
                           stdout=output,stderr=subprocess.STDOUT,check=True,timeout=600)
            subprocess.run(['uv','--no-config','pip','sync','--python',python,'--require-hashes',
                            '--only-binary',':all:',LOCK],stdin=subprocess.DEVNULL,
                           stdout=output,stderr=subprocess.STDOUT,check=True,timeout=1200)
        for item in catalog['files']:
            filename = item['path']
            target = model/filename
            local = Path(cache)/PROFILE/filename if cache else None
            if local and local.is_file() and not target.exists():
                if local.stat().st_size!=item['bytes'] or filehash(local)!=item['sha256']:
                    raise Error('asset_integrity',f'Cached Fluid asset does not match: {filename}.')
                staged = target.with_suffix(target.suffix+'.copy')
                try:
                    shutil.copyfile(local,staged)
                    staged.replace(target)
                finally:
                    staged.unlink(missing_ok=True)
            url = f'https://huggingface.co/{catalog["repository"]}/resolve/{catalog["revision"]}/{filename}'
            acquire(url,target,item['sha256'],item['bytes'],progress=progress,label=f'{PROFILE}/{filename}')
        progress.stage('Checking Fluid on a local speech sample')
        reference = ASSETS.parent/'cast_assets'/'nigel-thornberry'/'reference.wav'
        with TemporaryDirectory(prefix='hamr-fluid-smoke-') as scratch:
            pcm = Path(scratch)/'sample.pcm'
            subprocess.run(['ffmpeg','-v','error','-nostdin','-i',str(reference),'-t','4','-ac','1','-ar','24000',
                            '-f','s16le',str(pcm)],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE,check=True,timeout=30)
            report = observe(setup,pcm,{'sha256':filehash(pcm),'samples':pcm.stat().st_size//2})
            if 'smashing' not in report['text'].casefold():
                raise Error('asr_diagnostic_smoke','Fluid did not recognize the known speech in the local sample.')
    except (OSError,subprocess.SubprocessError) as exc:
        raise Error('asr_diagnostic_setup','Fluid diagnostic setup could not finish.',
                    f'Inspect {log}, then repeat hamr setup --diagnostic-asr fluid-int8.') from exc
    if activate:writejson(store.root/RECORD,setup)
    return setup
