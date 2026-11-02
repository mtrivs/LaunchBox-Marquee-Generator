"""Fast single-game media lookup for live previews; batch rendering keeps full indexes."""
import fnmatch,os,random,re
from pathlib import Path
from marquee import MediaIndex,EXTENSIONS,image_stem,key,safe_name,media_name,exclusion_reason

class PreviewCancelled(Exception): pass


def _entries(folder,pattern='*',directories=False):
    if os.name!='nt':
        if not folder.is_dir(): return []
        return [(p,0x10 if p.is_dir() else 0) for p in folder.iterdir() if fnmatch.fnmatch(p.name.casefold(),pattern.casefold()) and not p.is_symlink() and (p.is_dir() if directories else p.is_file())]
    import ctypes
    from ctypes import wintypes as w
    class FindData(ctypes.Structure):
        _fields_=[('attrs',w.DWORD),('created',w.FILETIME),('accessed',w.FILETIME),('written',w.FILETIME),('high',w.DWORD),('low',w.DWORD),('reserved0',w.DWORD),('reserved1',w.DWORD),('name',w.WCHAR*260),('short',w.WCHAR*14)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.FindFirstFileExW.argtypes=[w.LPCWSTR,ctypes.c_int,ctypes.POINTER(FindData),ctypes.c_int,ctypes.c_void_p,w.DWORD]; kernel.FindFirstFileExW.restype=w.HANDLE
    kernel.FindFirstFileW.argtypes=[w.LPCWSTR,ctypes.POINTER(FindData)]; kernel.FindFirstFileW.restype=w.HANDLE
    kernel.FindNextFileW.argtypes=[w.HANDLE,ctypes.POINTER(FindData)]; kernel.FindNextFileW.restype=w.BOOL
    kernel.FindClose.argtypes=[w.HANDLE]; kernel.FindClose.restype=w.BOOL
    data=FindData(); invalid=ctypes.c_void_p(-1).value
    # A server-side filename filter avoids listing a whole arcade image library.
    handle=kernel.FindFirstFileExW(str(folder/pattern),1,ctypes.byref(data),1 if directories else 0,None,2)
    if handle==invalid and ctypes.get_last_error() in [50,87,120]:
        handle=kernel.FindFirstFileW(str(folder/pattern),ctypes.byref(data))
    if handle==invalid:
        error=ctypes.get_last_error()
        if error in [2,3,18]: return []
        raise OSError(error,f'Cannot read media folder: {folder}')
    result=[]
    try:
        while True:
            is_directory=bool(data.attrs&0x10)
            if data.name not in ['.','..'] and is_directory==directories and not (directories and data.attrs&0x400):
                result.append((folder/data.name,data.attrs))
            if not kernel.FindNextFileW(handle,ctypes.byref(data)): break
    finally: kernel.FindClose(handle)
    return result


def regional_folders(folder,cache,cancelled=None):
    cache_key=str(folder)
    if cache_key in cache: return cache[cache_key]
    if not folder.is_dir():
        cache[cache_key]=[]; return []
    pending=[folder]; found=[]
    while pending:
        if cancelled and cancelled.is_set(): raise PreviewCancelled()
        current=pending.pop(); found.append(current)
        pending.extend(path for path,_ in _entries(current,directories=True))
    cache[cache_key]=sorted(found,key=lambda p:str(p).casefold())
    return cache[cache_key]


class PreviewMediaIndex(MediaIndex):
    def __init__(self,library,platform,game,folder_cache=None,cancelled=None):
        super().__init__(library,platform)
        self.game=game; self.folder_cache=folder_cache if folder_cache is not None else {}; self.cancelled=cancelled

    def folder(self,kind):
        """Cache configured-folder resolution, including missing folders."""
        cache_key=('media-folder',str(self.library.root),self.platform,kind)
        if cache_key not in self.folder_cache:
            self.folder_cache[cache_key]=super().folder(kind)
        return self.folder_cache[cache_key]

    def platform_source(self,kind,platform=None,logo_pack='auto'):
        """Reuse console-level discovery until the next explicit system scan."""
        cache_key=('platform-source',str(self.library.root),platform or self.platform,kind,logo_pack)
        if cache_key not in self.folder_cache:
            self.folder_cache[cache_key]=super().platform_source(kind,platform,logo_pack)
        return self.folder_cache[cache_key]

    def index(self,kind):
        if kind in self.indexes: return self.indexes[kind]
        exact,normalized={},{}
        aliases=self.game.aliases()+[self.game.id,self.game.database_id]
        patterns=set()
        for alias in aliases:
            if not alias or alias in ['0','-1']: continue
            cleaned=safe_name(alias)
            patterns.add(cleaned[:20]+'*')
            # Also cover LaunchBox's punctuation-only filename variations.
            first=re.match(r'\w+',cleaned)
            if first: patterns.add(first.group(0)[:20]+'*')
        paths=set()
        folders=regional_folders(self.folder(kind),self.folder_cache,self.cancelled)
        primary={name[:20]+'*' for name in [media_name(self.game.title),safe_name(self.game.title)]}
        def collect(queries):
            for folder in folders:
                for pattern in sorted(queries):
                    if self.cancelled and self.cancelled.is_set(): raise PreviewCancelled()
                    paths.update(path for path,_ in _entries(folder,pattern) if path.suffix.lower() in EXTENSIONS)
        collect(primary)
        stems={image_stem(path).casefold() for path in paths}
        title_aliases=list(dict.fromkeys([media_name(self.game.title),self.game.title,safe_name(self.game.title)]))
        if self.game.id: title_aliases += [name+'.'+self.game.id for name in title_aliases[:]]
        # Title matches outrank ROM/ID/normalized fallbacks, so most previews need
        # just one filtered query per regional folder.
        if not any(alias.casefold() in stems for alias in title_aliases): collect(patterns-primary)
        for path in sorted(paths,key=lambda p:str(p).casefold()):
            stem=image_stem(path)
            exact.setdefault(stem.casefold(),[]).append(path)
            normalized.setdefault(key(stem),[]).append(path)
        self.indexes[kind]=exact,normalized
        return self.indexes[kind]


def random_game(games,title_filter='',previous_id=None,rng=None):
    candidates=[game for game in games if not exclusion_reason(game) and title_filter.casefold() in game.title.casefold()]
    if not candidates: raise ValueError('No visible games match this system and title filter. Hidden and explicitly marked non-game entries are excluded.')
    previous_title=next((game.title.casefold() for game in games if previous_id is not None and game.id==previous_id),None)
    alternatives=[game for game in candidates if game.id!=previous_id and game.title.casefold()!=previous_title]
    if not alternatives: alternatives=[game for game in candidates if game.id!=previous_id]
    if alternatives: candidates=alternatives
    return (rng or random).choice(candidates)
