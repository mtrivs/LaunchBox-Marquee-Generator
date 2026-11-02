"""Small atomic writes and content verification helpers."""
import csv,hashlib,json,os,uuid
from pathlib import Path

def sha256(path):
    """Hash a file in bounded chunks without decoding it or loading it all.
    """
    result=hashlib.sha256()
    with Path(path).open('rb') as file:
        for block in iter(lambda:file.read(1024*1024),b''): result.update(block)
    return result.hexdigest()

def atomic_json(path,value):
    """Durably write JSON beside its destination, then replace atomically.

    The temporary file stays on the same filesystem. Errors propagate to the
    caller, and incomplete temporary files are removed on normal exception exit.
    """
    path=Path(path); temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('w',encoding='utf-8') as file:
            json.dump(value,file,ensure_ascii=False,indent=2); file.flush(); os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists(): temporary.unlink()

def atomic_csv(path,rows,fields):
    """Write a complete report to a sibling file before replacing the report.
    """
    path=Path(path); temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('w',newline='',encoding='utf-8-sig') as file:
            writer=csv.DictWriter(file,fieldnames=fields); writer.writeheader(); writer.writerows(rows); file.flush()
        temporary.replace(path)
    finally:
        if temporary.exists(): temporary.unlink()

def read_json(path,default):
    """Read a JSON value of the expected top-level type, or return a default.
    """
    try:
        value=json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value,type(default)) else default
    except (OSError,ValueError): return default

class OperationLock:
    """OS-held output lock; a crashed process releases it automatically."""
    def __init__(self,folder): self.path=Path(folder)/'.marquee-operation.lock'; self.file=None
    def __enter__(self):
        """Acquire a nonblocking OS lock; a second writer fails before mutations.
        """
        self.file=self.path.open('a+b'); self.file.seek(0)
        if self.path.stat().st_size==0: self.file.write(b'0'); self.file.flush(); self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close(); raise ValueError('Another operation is already using this export folder.')
        return self
    def __exit__(self,*args):
        """Release the OS-held file lock even when the operation raises.
        """
        if self.file:
            if os.name=='nt':
                import msvcrt
                self.file.seek(0); msvcrt.locking(self.file.fileno(),msvcrt.LK_UNLCK,1)
            self.file.close()

class LibraryLock(OperationLock):
    """Serialize installs across different exports targeting the same library.

    Lock files live in the user temporary directory, never in LaunchBox's ROM,
    metadata, or image folders. The OS releases a held lock on process exit.
    """
    def __init__(self,library_root):
        import tempfile
        identity=os.path.normcase(str(Path(library_root).resolve())).encode('utf-8')
        folder=Path(tempfile.gettempdir())/'MarqueeGeneratorLocks'/hashlib.sha256(identity).hexdigest()
        folder.mkdir(parents=True,exist_ok=True)
        super().__init__(folder)
