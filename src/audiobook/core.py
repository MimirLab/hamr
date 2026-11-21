from __future__ import annotations
import contextlib,hashlib,json,os,sqlite3,subprocess,tempfile,time
from dataclasses import dataclass,asdict,field
from pathlib import Path
from typing import Any

SCHEMA=16
RATE=24000
class Error(Exception):
    def __init__(self, code:str, message:str, action:str=''):
        self.code,self.message,self.action=code,message,action
        super().__init__(message)

def digest(data:bytes)->str: return hashlib.sha256(data).hexdigest()
def key(obj:Any)->str: return digest(json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode())
def filehash(p:Path)->str:
    with p.open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()
def dumps(x): return json.dumps(x,ensure_ascii=False,indent=2)+'\n'
def syncdir(p:Path):
    if os.name != 'nt':
        fd=os.open(p,os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
def syncfile(p:Path):
    # Windows FlushFileBuffers requires a handle with write access.
    with p.open('r+b' if os.name == 'nt' else 'rb') as f:
        os.fsync(f.fileno())
def atomic(p:Path,data:bytes):
    p.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.'+p.name,dir=p.parent)
    try:
        with os.fdopen(fd,'wb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
        if os.name=='nt':
            from .platform_support import windows_move
            windows_move(tmp,p,replace=True)
        else:
            os.replace(tmp,p); syncdir(p.parent)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
def writejson(p:Path,data): atomic(p,dumps(data).encode())
def loadjson(p:Path): return json.loads(p.read_text(encoding='utf-8'))


def check_revision(job, expected):
    if expected is not None and job['revision'] != expected:
        raise Error('revision_changed', f'Job revision is {job["revision"]}, expected {expected}.',
                    f'Inspect the current job with hamr status {job["id"]} --json.')

def require_matching_cli(cli:Path):
    """Qualification scripts must check the installed app before opening state."""
    from . import __version__
    try:
        result=subprocess.run([str(cli),'--version'],capture_output=True,text=True,timeout=20)
    except (OSError,subprocess.SubprocessError) as exc:
        raise Error('application_version','Could not verify the installed hamr version before opening state.') from exc
    if result.returncode or result.stdout.strip()!=__version__:
        raise Error('application_version',f'Qualification code requires hamr {__version__}; install the matching wheel before opening state.')
    return __version__

def run(args,**kwargs):
    try: return subprocess.run([str(x) for x in args],check=True,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**kwargs)
    except FileNotFoundError as e: raise Error('missing_dependency',f'Missing executable: {args[0]}',f'Install {args[0]} in user space, then run hamr doctor.') from e
    except subprocess.CalledProcessError as e: raise Error('subprocess_failed',f'{Path(str(args[0])).name} exited {e.returncode}', 'Check dependency/device availability and resume the job.') from e

def fault(point):
    # Explicit opt-in test seam; never interpreted from document content.
    if os.environ.get('AUDIOBOOK_FAULT')==point: os._exit(86)

@dataclass
class Block:
    id:str
    source:str
    locator:dict
    role:str='body'
    chapter:str='Opening'
    evidence:list[str]=field(default_factory=list)
    issue:str|None=None
    links:list[dict]=field(default_factory=list)

@dataclass
class Document:
    title:str
    format:str
    source_sha256:str
    blocks:list[Block]
    language:str='en'
    inventory:list[dict]=field(default_factory=list)
    warnings:list[str]=field(default_factory=list)
    metadata:dict=field(default_factory=dict)
    def json(self):
        value=asdict(self)
        if not self.metadata:value.pop('metadata')  # Saved legacy documents stay byte-compatible.
        return value

def state_root():
    """Honor explicit homes, then reuse existing state without moving pinned paths."""
    for name in ('HAMR_HOME', 'AUDIOBOOK_HOME'):
        if os.environ.get(name):
            return Path(os.environ[name])
    if os.name=='nt':
        return Path(os.environ.get('LOCALAPPDATA') or (Path.home()/'AppData/Local'))/'hamr'
    base = Path.home() / '.local/share'
    current, legacy = base / 'hamr', base / 'audiobook'
    if not (current / '.audiobook-owned').is_file() and (legacy / '.audiobook-owned').is_file():
        return legacy
    return current


class Store:
    def __init__(self,root:Path|None=None):
        self.root=Path(root if root is not None else state_root()).absolute()
        if self.root.is_symlink() or self.root.is_junction(): raise Error('unsafe_path','App state root cannot be a symlink or junction.')
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        if not (self.root/'.audiobook-owned').exists():
            if any(self.root.iterdir()): raise Error('unowned_directory','State directory is not empty and has no ownership marker.')
            atomic(self.root/'.audiobook-owned',b'audiobook v1\n')
        for part in ('jobs','clips','voices','assets','runtime','extractions','profiles'):
            p=self.root/part
            if p.is_symlink() or p.is_junction(): raise Error('unsafe_path','App directory cannot be a symlink or junction.')
            p.mkdir(exist_ok=True,mode=0o700)
        self.db=sqlite3.connect(self.root/'state.sqlite',timeout=10)
        self.db.execute('PRAGMA journal_mode=WAL'); self.db.execute('PRAGMA synchronous=FULL')
        version=self.db.execute('PRAGMA user_version').fetchone()[0]
        if version>SCHEMA: raise Error('newer_state','State schema is newer than this application.','Use the newer hamr build.')
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY,status TEXT NOT NULL,payload TEXT NOT NULL,updated REAL NOT NULL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS events (job TEXT,kind TEXT,at REAL)')
            self.db.execute(f'PRAGMA user_version={SCHEMA}')
    def save(self,j):
        j['updated']=time.time()
        with self.db:
            self.db.execute('INSERT INTO jobs VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,payload=excluded.payload,updated=excluded.updated',(j['id'],j['status'],dumps(j),j['updated']))
            self.db.execute('INSERT INTO events VALUES (?,?,?)',(j['id'],j['status'],j['updated']))
    def save_if_current(self,j,expected_updated):
        stamp=time.time(); payload={**j,'updated':stamp}
        with self.db:
            changed=self.db.execute('UPDATE jobs SET status=?,payload=?,updated=? WHERE id=? AND updated=?',(payload['status'],dumps(payload),stamp,j['id'],expected_updated)).rowcount
            if changed: self.db.execute('INSERT INTO events VALUES (?,?,?)',(j['id'],j['status'],stamp))
        if changed: j['updated']=stamp
        return bool(changed)
    def get(self,jid):
        row=self.db.execute('SELECT payload FROM jobs WHERE id=?',(jid,)).fetchone()
        if not row: raise Error('unknown_job','Unknown job ID.')
        return json.loads(row[0])
    def change_token(self):
        """A connection-local change hint, never a content verification result.

        data_version catches other connections' commits (including unchanged
        timestamps); total_changes also catches this connection's own writes.
        """
        return self.db.execute('PRAGMA data_version').fetchone()[0],self.db.total_changes
    def jobs(self): return [json.loads(r[0]) for r in self.db.execute('SELECT payload FROM jobs ORDER BY updated')]
    def path(self,j): return self.root/'jobs'/j['id']
    @contextlib.contextmanager
    def lock(self,name='writer',wait=False):
        p=self.root/'runtime'/(name+'.lock')
        if p.is_symlink() or p.is_junction(): raise Error('unsafe_path','Lock cannot be a symlink or junction.')
        fd=os.open(p,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            if os.name=='nt':
                from .platform_support import windows_lock
                try: windows_lock(fd,wait)
                except BlockingIOError as exc:
                    raise Error('busy','Another managed writer owns the runtime.','Wait for it to finish, then retry.') from exc
            else:
                import fcntl
                try: fcntl.flock(fd,fcntl.LOCK_EX|(0 if wait else fcntl.LOCK_NB))
                except BlockingIOError: raise Error('busy','Another managed writer owns the runtime.','Wait for it to finish, then retry.')
                os.ftruncate(fd,0); os.write(fd,str(os.getpid()).encode())
            yield
        finally: os.close(fd)
    def safe_remove(self,p:Path):
        import stat
        p=p.absolute()
        try: rel=p.relative_to(self.root)
        except ValueError: raise Error('unsafe_path','Cleanup target is outside app state.')
        if not rel.parts or '..' in rel.parts: raise Error('unsafe_path','Invalid cleanup path.')
        if os.name=='nt':
            from .windows_cleanup import remove_owned
            remove_owned(self.root, rel)
            return
        flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
        rootfd=os.open(self.root,flags); parents=[]
        def remove(parent,name):
            st=os.stat(name,dir_fd=parent,follow_symlinks=False)
            if stat.S_ISLNK(st.st_mode): raise Error('unsafe_path','Cleanup refused a symlink.')
            if stat.S_ISDIR(st.st_mode):
                child=os.open(name,flags,dir_fd=parent)
                try:
                    current=os.fstat(child)
                    if (current.st_dev,current.st_ino)!=(st.st_dev,st.st_ino): raise Error('unsafe_path','Cleanup target changed during inspection.')
                    for entry in os.listdir(child): remove(child,entry)
                finally: os.close(child)
                os.rmdir(name,dir_fd=parent)
            else: os.unlink(name,dir_fd=parent)
        try:
            fd=rootfd
            for part in rel.parts[:-1]:
                fd=os.open(part,flags,dir_fd=fd); parents.append(fd)
            remove(fd,rel.parts[-1])
        except OSError as e:
            if e.errno in (20,40): raise Error('unsafe_path','Cleanup refused a substituted path or symlink.') from e
            raise
        finally:
            for fd in reversed(parents): os.close(fd)
            os.close(rootfd)
