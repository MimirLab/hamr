"""Bounded child-process Vulkan inventory. No logical device or GPU work is created."""
import ctypes as C
import ctypes.util
import json


U32 = C.c_uint32
U64 = C.c_uint64
PTR = C.c_void_p


class Application(C.Structure):
    _fields_ = [('sType', U32), ('pNext', PTR), ('name', C.c_char_p), ('version', U32),
                ('engine', C.c_char_p), ('engineVersion', U32), ('apiVersion', U32)]


class InstanceInfo(C.Structure):
    _fields_ = [('sType', U32), ('pNext', PTR), ('flags', U32), ('application', C.POINTER(Application)),
                ('layers', U32), ('layerNames', PTR), ('extensions', U32), ('extensionNames', PTR)]


class MemoryType(C.Structure):
    _fields_ = [('flags', U32), ('heap', U32)]


class Heap(C.Structure):
    _fields_ = [('size', U64), ('flags', U32)]


class Memory(C.Structure):
    _fields_ = [('typeCount', U32), ('types', MemoryType * 32), ('heapCount', U32), ('heaps', Heap * 16)]


class Budget(C.Structure):
    _fields_ = [('sType', U32), ('pNext', PTR), ('budget', U64 * 16), ('usage', U64 * 16)]


class Memory2(C.Structure):
    _fields_ = [('sType', U32), ('pNext', PTR), ('memory', Memory)]


class Extension(C.Structure):
    _fields_ = [('name', C.c_char * 256), ('version', U32)]


class Properties(C.Structure):
    # Full aligned backing storage includes the fixed limits/sparse-properties
    # tail. Passing just a short prefix to the driver would corrupt memory.
    _fields_ = [('api', U32), ('driver', U32), ('vendor', U32), ('device', U32),
                ('kind', U32), ('name', C.c_char * 256), ('uuid', C.c_ubyte * 16),
                ('tail', U64 * 512)]


def function(lib, name, restype, *args):
    fn = getattr(lib, name)
    fn.restype, fn.argtypes = restype, list(args)
    return fn


def probe():
    import os
    lib = C.WinDLL('vulkan-1.dll') if os.name=='nt' else C.CDLL(ctypes.util.find_library('vulkan') or 'libvulkan.so.1')
    version = U32(1 << 22)
    if hasattr(lib, 'vkEnumerateInstanceVersion'):
        function(lib, 'vkEnumerateInstanceVersion', C.c_int, C.POINTER(U32))(C.byref(version))
    api = min(version.value, (1 << 22) | (1 << 12))
    app = Application(0, None, b'hamr doctor', 1, b'hamr inventory', 1, api)
    info = InstanceInfo(1, None, 0, C.pointer(app), 0, None, 0, None)
    instance = PTR()
    result = function(lib, 'vkCreateInstance', C.c_int, C.POINTER(InstanceInfo), PTR, C.POINTER(PTR))(C.byref(info), None, C.byref(instance))
    if result:
        return {'status': 'unavailable', 'loader_found': True, 'reason': f'Vulkan instance creation failed (VkResult {result}); check the vendor ICD.', 'devices': []}
    destroy = function(lib, 'vkDestroyInstance', None, PTR, PTR)
    try:
        enumerate_devices = function(lib, 'vkEnumeratePhysicalDevices', C.c_int, PTR, C.POINTER(U32), C.POINTER(PTR))
        count = U32()
        result = enumerate_devices(instance, C.byref(count), None)
        if result or not 0 < count.value <= 64:
            return {'status': 'unavailable', 'loader_found': True, 'reason': 'No physical Vulkan devices could be enumerated.', 'devices': []}
        devices = (PTR * count.value)()
        result = enumerate_devices(instance, C.byref(count), devices)
        if result not in (0, 5):
            raise RuntimeError(f'Device enumeration failed: {result}')
        properties = function(lib, 'vkGetPhysicalDeviceProperties', None, PTR, C.POINTER(Properties))
        memory = function(lib, 'vkGetPhysicalDeviceMemoryProperties', None, PTR, C.POINTER(Memory))
        extensions = function(lib, 'vkEnumerateDeviceExtensionProperties', C.c_int, PTR, C.c_char_p, C.POINTER(U32), C.POINTER(Extension))
        rows = []
        for device in devices[:count.value]:
            p, m = Properties(), Memory()
            properties(device, C.byref(p)); memory(device, C.byref(m))
            local = [i for i in range(min(m.heapCount, 16)) if m.heaps[i].flags & 1]
            budget = None
            extension_count = U32()
            if api >= (1 << 22) | (1 << 12) and hasattr(lib, 'vkGetPhysicalDeviceMemoryProperties2') and extensions(device, None, C.byref(extension_count), None) == 0 and 0 < extension_count.value <= 4096:
                available = (Extension * extension_count.value)()
                code = extensions(device, None, C.byref(extension_count), available)
                if code in (0, 5) and any(e.name == b'VK_EXT_memory_budget' for e in available):
                    b = Budget(1000237000, None)
                    m2 = Memory2(1000059006, C.cast(C.pointer(b), PTR), m)
                    function(lib, 'vkGetPhysicalDeviceMemoryProperties2', None, PTR, C.POINTER(Memory2))(device, C.byref(m2))
                    budget = sum(max(0, b.budget[i]-b.usage[i]) for i in local)
            rows.append({'name': p.name.decode('utf-8', 'replace'), 'type': {1: 'INTEGRATED_GPU', 2: 'DISCRETE_GPU', 3: 'VIRTUAL_GPU', 4: 'CPU'}.get(p.kind, 'OTHER'),
                         'vendor_id': p.vendor, 'device_id': p.device, 'vram_bytes': sum(m.heaps[i].size for i in local),
                         'available_budget_bytes': budget, 'api_version': f'{p.api >> 22}.{(p.api >> 12) & 1023}.{p.api & 4095}'})
        real = [d for d in rows if d['type'] in ('INTEGRATED_GPU', 'DISCRETE_GPU', 'VIRTUAL_GPU')]
        return {'status': 'available' if real else 'unavailable', 'loader_found': True, 'devices': rows,
                'reason': 'Driver inventory completed without a logical device, model load or GPU dispatch.' if real else 'Only software Vulkan devices were found.',
                'logical_device_created': False, 'gpu_work_submitted': False}
    finally:
        destroy(instance, None)


if __name__ == '__main__':
    try:
        print(json.dumps(probe()))
    except OSError as exc:
        print(json.dumps({'status': 'unavailable', 'loader_found': False, 'reason': str(exc), 'devices': [], 'logical_device_created': False, 'gpu_work_submitted': False}))
    except (AttributeError, RuntimeError) as exc:
        print(json.dumps({'status': 'unavailable', 'loader_found': True, 'reason': str(exc), 'devices': [], 'logical_device_created': False, 'gpu_work_submitted': False}))
