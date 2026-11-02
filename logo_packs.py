"""Read installed platform clear-logo packs; no downloads or source changes."""
from pathlib import Path
import os
import re
EXTENSIONS={'.png','.jpg','.jpeg','.webp','.bmp','.tif','.tiff'}
AUTO='Automatic (existing folder first)'

def normal(value): return re.sub(r'[^\w]','',value.casefold()).replace('_','')

def packs(library):
    """List installed clear-logo pack folders in a stable display order.
    """
    root=library.root/'Images'/'Media Packs'/'Platform Clear Logos'
    if not root.is_dir(): return {}
    return {p.name:p for p in sorted(root.iterdir(),key=lambda p:p.name.casefold()) if p.is_dir() and not p.is_symlink()}

def pack_image(folder,names):
    # Limit lookup to platform branches, avoiding identically named playlists.
    """Find a unique platform-name match in supported platform branches.

    Playlist/category branches are excluded. Custom platform names may also
    use the library scrape-as name; ambiguous filenames are not guessed.
    """
    branches=[folder,folder/'Platforms',folder/'Platform',folder/'Platform Clear Logos']
    wanted=[normal(name) for name in names if name]
    for branch in branches:
        if not branch.is_dir(): continue
        # DirEntry reuses directory metadata instead of issuing an is_file
        # request for each logo across SMB. Ignore unrelated suffixes first.
        with os.scandir(branch) as entries:
            files=sorted((Path(entry.path) for entry in entries if Path(entry.name).suffix.lower() in EXTENSIONS and entry.is_file()),key=lambda p:p.name.casefold())
        for name in wanted:
            exact=[p for p in files if normal(p.stem)==name]
            if len(exact)==1: return exact[0]
    return None

def find_logo(library,platform,choice='auto'):
    """Resolve an existing override or installed pack without modifying either.

    Automatic selection keeps the legacy platform folder first, then tries
    Nostalgic, legacy conversion, and remaining packs. An explicit pack choice
    precedes the existing folder, with automatic fallback for missing images.
    """
    from marquee import safe_name
    legacy=library.root/'Images'/'Platforms'/safe_name(platform)/'Clear Logo'
    old=next((p for p in sorted(legacy.iterdir(),key=lambda p:p.name.casefold()) if p.is_file() and p.suffix.lower() in EXTENSIONS),None) if legacy.is_dir() else None
    automatic=choice in ('','auto',AUTO,None)
    # A legacy override completes automatic lookup; do not enumerate every
    # installed pack, especially when the library is on a network share.
    if automatic and old: return old
    available=packs(library); names=[platform,library.scrape_as.get(platform,'')]
    if not automatic:
        folder=next((path for name,path in available.items() if name.casefold()==choice.casefold()),None)
        if folder:
            found=pack_image(folder,names)
            if found: return found
        if old: return old
    order=sorted(available,key=lambda name:('nostalg' not in name.casefold(),'legacy' not in name.casefold(),name.casefold()))
    for name in order:
        found=pack_image(available[name],names)
        if found: return found
    return old
