"""Reviewed installation with same-folder preservation and reversible backups."""
import hashlib,json,os,re,shutil,uuid
from datetime import datetime
from pathlib import Path
from PIL import Image
import marquee as m
from io_utils import OperationLock,LibraryLock,atomic_json,read_json,sha256
GUID=re.compile(r'(?i)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')

def inside(path,root):
    """Test containment using resolved paths, including symlink/junction targets.
    """
    try: Path(path).resolve().relative_to(Path(root).resolve()); return True
    except ValueError: return False

def checked_child(root,relative):
    """Resolve a relative manifest member while rejecting root/path escapes.
    """
    root=Path(root).resolve(); path=root/relative
    if Path(relative).is_absolute() or not inside(path,root) or path.resolve()==root: raise ValueError('A manifest path escapes its permitted folder.')
    return path

def reparse(path):
    """Identify symlinks and Windows reparse points without following the file.
    """
    try: return path.is_symlink() or bool(getattr(path.lstat(),'st_file_attributes',0)&0x400)
    except OSError: return False

def image_files(folder):
    """Enumerate supported image files without descending into redirected dirs.
    """
    folder=Path(folder)
    if not folder.is_dir(): return []
    found=[]
    for parent,dirs,files in os.walk(folder,followlinks=False):
        dirs[:]=sorted(d for d in dirs if not reparse(Path(parent)/d))
        for filename in sorted(files,key=str.casefold):
            path=Path(parent)/filename
            if path.suffix.lower() in m.EXTENSIONS: found.append(path)
    return found

def lookup_games(games):
    """Build indexed ownership checks for exact, GUID and stripped-title names.

    All imported records participate, including hidden records. This makes a
    shared image a conflict rather than renaming artwork belonging to a game
    without a verified replacement. Stripped matching is intentionally
    conservative and does not claim to reproduce every LaunchBox fuzzy rule.
    """
    exact={}; stripped={}; ids={g.id.casefold():g for g in games if g.id}
    def normalized_title(value):
        value=re.sub(r'\([^)]*\)|\[[^]]*\]|\{[^}]*\}','',value)
        value=re.sub(r'(?i)\b(?:a|an|and|the)\b','',value)
        return m.key(value)
    for game in games:
        if not game.id: continue
        for alias in game.aliases(): exact.setdefault(alias.casefold(),set()).add(game.id)
        stripped.setdefault(normalized_title(game.title),set()).add(game.id)
    def owners(path):
        identifiers={ids[g.casefold()].id for g in GUID.findall(path.stem) if g.casefold() in ids}
        numbered=bool(re.search(r'-\d+$',path.stem))
        stem=re.sub(r'-\d+$','',path.stem)
        matched=identifiers | exact.get(stem.casefold(),set())
        # LaunchBox's stripped-title fallback applies to unnumbered media. Union
        # its possible owners with exact/GUID owners: a strong match for one
        # game must never hide a plausible match for another imported game.
        if not numbered: matched=matched | stripped.get(normalized_title(path.stem),set())
        return matched
    return owners

def inactive_path(path,digest,kind='original'):
    """Validate the legacy version 2 opaque naming scheme during restore.

    Changing the final extension, rather than adding a title suffix before
    .png, prevents title/ROM/GUID matching from selecting preserved artwork.
    The durable journal retains the full original name and content hash.
    """
    path=Path(path)
    token=hashlib.sha256((str(path.resolve())+'\0'+digest).encode()).hexdigest()[:16]
    return path.with_name(f'.marquee-{kind}-{token}.disabled')


class NumberedNames:
    """Reserve append-only image numbers from one directory scan per folder.

    Numbers are shared across image formats and compared case-insensitively.
    Existing gaps are retained: with -01 and -03 present, the next number is
    -04. Reservations also prevent collisions among actions in the same plan.
    """
    def __init__(self):
        self.folders={}

    def next(self,path):
        """Return and reserve the next numbered sibling without creating it."""
        path=Path(path); folder=path.parent.resolve()
        if folder not in self.folders:
            names=set(); highest={}
            if folder.is_dir():
                for child in folder.iterdir():
                    names.add(child.name.casefold())
                    match=re.fullmatch(r'(.+)-(\d+)',child.stem)
                    if match and child.suffix.lower() in m.EXTENSIONS:
                        base=match[1].casefold()
                        highest[base]=max(highest.get(base,0),int(match[2]))
            self.folders[folder]=(names,highest)
        names,highest=self.folders[folder]; base=path.stem.casefold()
        number=highest.get(base,0)+1
        while True:
            candidate=path.with_name(f'{path.stem}-{number:02d}{path.suffix}')
            if len(str(candidate))>248 or len(candidate.name)>240:
                raise ValueError('A numbered preservation filename would be too long; manual review is needed.')
            if candidate.name.casefold() not in names: break
            number+=1
        highest[base]=number; names.add(candidate.name.casefold())
        return candidate


def numbered_variant(original,preserved):
    """Validate a same-folder numbered sibling without trusting a journal path."""
    original=Path(original); preserved=Path(preserved)
    return (original.parent.resolve()==preserved.parent.resolve() and
            original.suffix.casefold()==preserved.suffix.casefold() and
            re.fullmatch(re.escape(original.stem)+r'-[0-9]+',preserved.stem,re.IGNORECASE) is not None)


def rename_verified(source,destination,digest):
    """Rename within one directory without replacing an existing filename."""
    source=Path(source); destination=Path(destination)
    if source.parent.resolve()!=destination.parent.resolve(): raise ValueError('Preserved artwork must stay in its original folder.')
    if reparse(source) or reparse(destination) or sha256(source)!=digest: raise ValueError('Artwork changed before rename; review again.')
    # Windows rename refuses occupied targets. The POSIX equivalent provides
    # the same guarantee; the source is unlinked only after its bytes are linked.
    if os.name=='nt': os.rename(source,destination)
    else: os.link(source,destination); source.unlink()


def installed_filename(game,folder,duplicate=False):
    """Use an unnumbered title when unique, preserving GUID disambiguation."""
    filename=m.marquee_filename(game,folder,duplicate)
    if not duplicate and not GUID.search(filename) and filename.endswith('-01.png'):
        return filename[:-7]+'.png'
    return filename


def plan_stamp(entry):
    """Fingerprint the reviewed sources, destinations and older-file hashes.
    """
    return hashlib.sha256(json.dumps({key:entry[key] for key in ['platform','game_id','source','sha256','destination','older','retained']},sort_keys=True).encode()).hexdigest()

def build_plan(library,output,platforms=None):
    """Create a read-only review from verified exports and current game records.

    Only the current configured Arcade - Marquee folders are inspected.
    Each replacement must have a matching manifest hash, current game ID,
    canonical export filename, and valid PNG header/dimensions. Ready, blocked
    and already-installed entries are returned without changing any artwork.
    """
    output=Path(output).resolve()
    if inside(output,library.root): raise ValueError('Choose an export folder outside LaunchBox.')
    manifest=read_json(output/'marquee-manifest.json',{})
    if manifest.get('version')!=1 or not isinstance(manifest.get('images'),dict):
        raise ValueError('No verified export manifest found. Generate or verify artwork with version 2.1 or later first.')
    wanted=set(platforms or library.platform_files)
    entries=[]; by_platform={}; numbering=NumberedNames()
    for relative,item in sorted(manifest['images'].items()):
        if not isinstance(item,dict) or item.get('platform') not in wanted: continue
        platform=item['platform']; entry={'platform':platform,'game_id':item.get('game_id',''),'title':item.get('title',''),'ready':False,'reason':'','older':[]}
        try:
            if platform not in by_platform:
                games=library.games(platform); counts={}
                for game in games:
                    key=m.media_name(game.title).casefold(); counts[key]=counts.get(key,0)+1
                staging,folder,_=m.marquee_export_folder(library,platform,output)
                if inside(folder,output): raise ValueError('The configured marquee folder overlaps this export. Choose a separate export folder.')
                owner=lookup_games(games); owned={}; ambiguous={}
                # Index each installed filename once. Scanning every image for
                # every game becomes quadratic on large arcade collections.
                for candidate in image_files(folder):
                    matches=owner(candidate)
                    for game_id in matches:
                        if len(matches)>1: ambiguous[game_id]=candidate.name
                        else: owned.setdefault(game_id,[]).append(candidate)
                by_platform[platform]=({g.id:g for g in games if g.id},counts,staging,folder,owned,ambiguous)
            games,counts,staging,folder,owned,ambiguous=by_platform[platform]
            game=games.get(entry['game_id'])
            if game is None or not game.id: raise ValueError('The game ID is missing from the current library.')
            if m.exclusion_reason(game): raise ValueError('This entry is now hidden or excluded.')
            source=checked_child(output,relative)
            filename=m.marquee_filename(game,staging,counts[m.media_name(game.title).casefold()]>1)
            has_game_guid=bool(GUID.fullmatch(game.id) and game.id.casefold() in source.stem.casefold())
            if source.parent!=staging or (source.name!=filename and not has_game_guid): raise ValueError('The export name no longer matches the current game record; regenerate it.')
            if reparse(source) or not source.is_file() or sha256(source)!=item.get('sha256'): raise ValueError('Generated image is missing, changed, or redirected.')
            with Image.open(source) as image:
                if image.format!='PNG' or image.size!=m.SIZE: raise ValueError('The replacement is not a 1920 x 360 PNG.')
            # Destination and staging paths can have different length budgets.
            # A verified GUID-named export can move between PCs without requiring
            # a rerender; derive the installed name for the actual destination.
            destination=folder/installed_filename(game,folder,counts[m.media_name(game.title).casefold()]>1)
            if not inside(destination,folder): raise ValueError('Marquee destination escapes its configured folder.')
            older=[]; retained=[]
            if game.id in ambiguous: raise ValueError(f'Shared or ambiguous older image: {ambiguous[game.id]}. It needs manual review.')
            for path in owned.get(game.id,[]):
                if reparse(path) or not inside(path,folder): raise ValueError(f'Redirected older image needs manual review: {path.name}')
                digest=sha256(path)
                if path.stem.casefold()==destination.stem.casefold():
                    preserved=numbering.next(path)
                    older.append({'path':str(path.resolve()),'sha256':digest,'preserved':str(preserved.resolve())})
                else:
                    # Numbered, ROM-named and GUID alternatives remain active
                    # images. Only the unnumbered title slots are displaced.
                    retained.append({'path':str(path.resolve()),'sha256':digest})
            if destination.exists() and str(destination.resolve()) not in {o['path'] for o in older}:
                raise ValueError('The target filename belongs to an unrecognized image; manual review is needed.')
            # Existing numbered alternatives are intentionally retained. Do not
            # renumber an already-installed generated title on a repeat run.
            if len(older)==1 and any(Path(old['path'])==destination.resolve() and old['sha256']==item['sha256'] for old in older):
                entry.update(reason='Already installed; no changes needed.',status='unchanged')
            else:
                entry.update(ready=True,reason='Rename unnumbered title images to the next available numbers; keep existing alternatives and install the generated title image.',status='ready')
            entry.update(title=game.title,source=str(source),sha256=item['sha256'],destination=str(destination.resolve()),folder=str(folder.resolve()),older=older,retained=retained)
            entry['stamp']=plan_stamp(entry)
        except (OSError,ValueError,KeyError) as error: entry.update(reason=str(error),status='blocked')
        entries.append(entry)
    if not entries: raise ValueError('No verified replacements are available for the selected systems.')
    return {'version':1,'library':str(library.root.resolve()),'export':str(output),'entries':entries}

def copy_verified(source,destination,digest):
    """Copy through a sibling temporary file and refuse an occupied target.

    Content is flushed and verified before promotion. Windows rename and the
    POSIX hard-link equivalent both avoid replacing a target that appeared
    after review; this is essential for safe install and restore recovery.
    """
    source=Path(source); destination=Path(destination); destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=destination.with_name('.marquee-'+uuid.uuid4().hex+'.tmp')
    try:
        with source.open('rb') as src,temporary.open('xb') as dst:
            shutil.copyfileobj(src,dst,1024*1024); dst.flush(); os.fsync(dst.fileno())
        if sha256(temporary)!=digest: raise ValueError('A file changed while copying; the operation stopped.')
        # Refuse a target created after review rather than replacing it.
        if os.name=='nt': os.rename(temporary,destination)
        else: os.link(temporary,destination); temporary.unlink()
    finally:
        if temporary.exists(): temporary.unlink()

def apply_plan(library,output,plan,backup_parent,progress=None):
    """Install reviewed replacements only after every older image is backed up.

    The library and export are locked, then the full review is rebuilt to
    reject changed files/settings. A dated backup folder is created at the
    location explicitly chosen for this operation. Recovery status is saved
    before the first rename and after each game, so partial installs remain
    restorable. This function never alters ROMs or LaunchBox metadata.
    """
    output=Path(output).resolve(); backup_parent=Path(backup_parent).resolve()
    if plan.get('library')!=str(library.root.resolve()) or plan.get('export')!=str(output): raise ValueError('This review belongs to a different library or export.')
    selected=[entry for entry in plan['entries'] if entry.get('ready')]
    if not selected: raise ValueError('There are no reviewed changes to install.')
    if inside(backup_parent,library.root): raise ValueError('Choose a backup folder outside the LaunchBox installation.')
    for entry in selected:
        if inside(backup_parent,entry['folder']) or inside(backup_parent,Path(entry['source']).parent): raise ValueError('Backups must stay outside marquee and generated-image folders.')
    with LibraryLock(library.root),OperationLock(output):
        fresh=build_plan(library,output,{e['platform'] for e in selected})
        current={(e['platform'],e['game_id'],e.get('source')):e for e in fresh['entries']}
        for entry in selected:
            found=current.get((entry['platform'],entry['game_id'],entry['source']),{})
            if not found.get('ready') or found.get('stamp')!=entry.get('stamp'): raise ValueError('Images or settings changed after review. Review installation again.')
        selected=[current[(entry['platform'],entry['game_id'],entry['source'])] for entry in selected]
        backup_parent.mkdir(parents=True,exist_ok=True)
        backup=backup_parent/('Marquee backup '+datetime.now().strftime('%Y-%m-%d %H%M%S')+'-'+uuid.uuid4().hex[:6]); backup.mkdir()
        journal={'version':3,'library':str(library.root.resolve()),'export':str(output),'operation_id':uuid.uuid4().hex,'status':'backing-up','entries':[]}
        manifest=backup/'restore-manifest.json'
        for i,entry in enumerate(selected):
            record={key:entry[key] for key in ['platform','game_id','title','source','sha256','destination','folder']}; record.update(older=[],status='pending',generated_preserved=None)
            for j,old in enumerate(entry['older']):
                relative=f'files/{i:06d}-{j:06d}{Path(old["path"]).suffix.lower()}'
                record['older'].append(dict(old,backup=relative))
            journal['entries'].append(record)
        atomic_json(manifest,journal)
        # Finish and verify every backup before renaming any installed image.
        for record in journal['entries']:
            for old in record['older']:
                if sha256(old['path'])!=old['sha256']: raise ValueError('An installed image changed before backup; review again.')
                copy_verified(old['path'],checked_child(backup,old['backup']),old['sha256'])
        journal['status']='installing'; atomic_json(manifest,journal)
        try:
            for i,record in enumerate(journal['entries'],1):
                # Recheck this entry immediately before the reviewed mutations.
                if sha256(record['source'])!=record['sha256']: raise ValueError('Generated artwork changed after review.')
                for old in record['older']:
                    path=Path(old['path'])
                    if reparse(path) or not inside(path,record['folder']) or sha256(path)!=old['sha256']: raise ValueError('An installed image changed; installation stopped.')
                record['status']='installing'; atomic_json(manifest,journal)
                for old in record['older']: rename_verified(old['path'],old['preserved'],old['sha256'])
                destination=Path(record['destination'])
                if destination.exists(): raise ValueError('A new target appeared during installation; restore from the recorded backup.')
                copy_verified(record['source'],destination,record['sha256'])
                record['status']='installed'; atomic_json(manifest,journal)
                if progress: progress(i,len(selected),record['title'])
            journal['status']='installed'; atomic_json(manifest,journal)
        except Exception as error:
            journal['status']='interrupted'; journal['error']=str(error); atomic_json(manifest,journal)
            raise ValueError(f'Installation stopped. Verified backups and a restore manifest are in {backup}. {error}') from error
    return backup

def _restore_entry(library,backup,journal,number,numbering=None):
    """Validate one game without rescanning other entries' backup contents."""
    if journal.get('version') in (2,3): return _restore_preserved_entry(library,backup,journal,number,numbering)
    item=journal['entries'][number]
    entry={'number':number,'title':item.get('title',''),'ready':False,'reason':''}
    try:
        if item.get('status') in ('pending','restored'): raise ValueError('No installed changes to restore for this entry.')
        _,folder,_=m.marquee_export_folder(library,item['platform'],Path(journal['export']))
        if folder.resolve()!=Path(item['folder']).resolve(): raise ValueError('The configured marquee folder changed; restore needs manual review.')
        destination=Path(item['destination'])
        if not inside(destination,folder) or destination.suffix.lower()!='.png': raise ValueError('Invalid restore target.')
        expected={o['path']:o['sha256'] for o in item['older']}
        if destination.exists() and sha256(destination)!=item['sha256']:
            if expected.get(str(destination))!=sha256(destination): raise ValueError('Installed artwork changed after installation; it will not be overwritten.')
        for old in item['older']:
            path=Path(old['path']); stored=checked_child(backup,old['backup'])
            if not inside(path,folder) or path.suffix.lower() not in m.EXTENSIONS or reparse(path) or reparse(stored): raise ValueError('Invalid or redirected backup path.')
            if not stored.is_file() or sha256(stored)!=old['sha256']: raise ValueError('A backup is missing or changed.')
            if path.exists() and sha256(path)!=old['sha256'] and path!=destination: raise ValueError('A newer image occupies an original filename; it will not be overwritten.')
        entry.update(ready=True,reason='Remove unchanged installed artwork and restore original images.')
    except (OSError,ValueError,KeyError) as error: entry['reason']=str(error)
    return entry


def _restore_preserved_entry(library,backup,journal,number,numbering=None):
    """Validate interrupted or complete same-folder installs without mutations."""
    item=journal['entries'][number]
    entry={'number':number,'title':item.get('title',''),'ready':False,'reason':''}
    try:
        if item.get('status') in ('pending','restored'): raise ValueError('No installed changes to restore for this entry.')
        _,folder,_=m.marquee_export_folder(library,item['platform'],Path(journal['export']))
        if folder.resolve()!=Path(item['folder']).resolve(): raise ValueError('The configured marquee folder changed; restore needs manual review.')
        destination=Path(item['destination'])
        generated=Path(item['generated_preserved']) if item.get('generated_preserved') else (numbering or NumberedNames()).next(destination)
        if not inside(destination,folder) or destination.suffix.lower()!='.png' or reparse(destination): raise ValueError('Invalid or redirected restore target.')
        valid_generated=(numbered_variant(destination,generated) if journal.get('version')==3 else generated==inactive_path(destination,item['sha256']+journal['operation_id'],'generated'))
        if not valid_generated or reparse(generated): raise ValueError('Invalid generated-art preservation path.')
        if generated.exists() and sha256(generated)!=item['sha256']: raise ValueError('Preserved generated artwork changed; manual review is needed.')
        expected={old['path']:old['sha256'] for old in item['older']}
        if destination.exists():
            current=sha256(destination)
            if current!=item['sha256'] and current!=expected.get(str(destination)): raise ValueError('Installed artwork changed after installation; it will not be overwritten.')
            if current==item['sha256'] and current!=expected.get(str(destination)) and generated.exists(): raise ValueError('The generated preservation filename is occupied; manual review is needed.')
        for old in item['older']:
            path=Path(old['path']); preserved=Path(old['preserved']); stored=checked_child(backup,old['backup'])
            if not inside(path,folder) or path.suffix.lower() not in m.EXTENSIONS or reparse(path) or reparse(preserved) or reparse(stored): raise ValueError('Invalid or redirected original path.')
            valid_original=(numbered_variant(path,preserved) if journal.get('version')==3 else preserved==inactive_path(path,old['sha256']))
            if not valid_original: raise ValueError('Invalid original-art preservation path.')
            if not stored.is_file() or sha256(stored)!=old['sha256']: raise ValueError('A backup is missing or changed.')
            if preserved.exists() and sha256(preserved)!=old['sha256']: raise ValueError('Preserved original artwork changed; manual review is needed.')
            if path.exists() and sha256(path)!=old['sha256'] and path!=destination: raise ValueError('A newer image occupies an original filename; it will not be overwritten.')
        entry.update(ready=True,generated_preserved=str(generated),reason='Preserve unchanged generated artwork in place and restore original filenames.')
    except (OSError,ValueError,KeyError) as error: entry['reason']=str(error)
    return entry


def _restore_preserved_files(backup,item):
    """Keep both generations: rename the generated PNG, then reactivate originals.

    The complete mapping is durable before mutation. Filesystem/hash checks
    make a retry safe even if power was lost between a rename and journal save.
    External backups recover originals if a preserved file was removed later.
    """
    destination=Path(item['destination'])
    expected={old['path']:old['sha256'] for old in item['older']}
    if destination.exists() and sha256(destination)==item['sha256'] and (not Path(item['generated_preserved']).exists() or expected.get(str(destination))!=item['sha256']):
        rename_verified(destination,item['generated_preserved'],item['sha256'])
    for old in item['older']:
        path=Path(old['path']); preserved=Path(old['preserved'])
        if path.exists():
            if sha256(path)!=old['sha256']: raise ValueError('An original filename changed during restore.')
            continue
        if preserved.exists(): rename_verified(preserved,path,old['sha256'])
        else: copy_verified(checked_child(backup,old['backup']),path,old['sha256'])


def review_restore(library,backup):
    """Build a non-mutating restore review; edited targets become conflicts."""
    backup=Path(backup).resolve(); journal=read_json(backup/'restore-manifest.json',{})
    if journal.get('version') not in (1,2,3) or journal.get('library')!=str(library.root.resolve()): raise ValueError('This is not a backup for the selected LaunchBox installation.')
    if not isinstance(journal.get('entries'),list): raise ValueError('The restore manifest has no valid entries.')
    if inside(backup,library.root) or inside(journal.get('export',''),library.root): raise ValueError('Backup and export folders must remain outside LaunchBox.')
    numbering=NumberedNames()
    entries=[_restore_entry(library,backup,journal,n,numbering) for n in range(len(journal['entries']))]
    return {'version':1,'backup':str(backup),'entries':entries,'journal':journal}


def restore_backup(library,review,progress=None):
    """Restore selected reviewed entries while preserving newer artwork.

    The reviewed manifest must be unchanged. Targets and backup hashes are
    rechecked immediately before each game. Version 2/3 journals preserve the
    generated image in place and reactivate original filenames. Version 1
    journals retain the older backup-based restore behavior. Neither path
    replaces files that appeared after review.
    """
    backup=Path(review['backup']); fresh=review_restore(library,backup)
    if fresh['journal']!=review.get('journal'): raise ValueError('The backup manifest changed after review. Review again.')
    selected=[entry['number'] for entry in review['entries'] if entry['ready']]
    if not selected: raise ValueError('There are no safe restore actions in this review.')
    if any(not fresh['entries'][n]['ready'] for n in selected): raise ValueError('Files changed after the restore review. Review again.')
    reviewed_entries={entry['number']:entry for entry in review['entries']}
    for n in selected:
        approved=reviewed_entries[n]
        if fresh['entries'][n].get('generated_preserved')!=approved.get('generated_preserved'):
            raise ValueError('Numbered filenames changed after restore review. Review again.')
    journal=fresh['journal']; output=Path(journal['export']); output.mkdir(parents=True,exist_ok=True)
    with LibraryLock(library.root),OperationLock(output):
        for i,n in enumerate(selected,1):
            item=journal['entries'][n]; destination=Path(item['destination'])
            # Freeze the reviewed number before revalidation. No-replace rename
            # still guards files created after the directory snapshot, without
            # rescanning a large system folder for every game in the restore.
            if journal.get('version') in (2,3): item['generated_preserved']=fresh['entries'][n]['generated_preserved']
            # Revalidate every entry before touching it, including conflict guards.
            if not _restore_entry(library,backup,journal,n)['ready']: raise ValueError('A restore conflict appeared; review again.')
            if journal.get('version') in (2,3):
                item['status']='restoring'; atomic_json(backup/'restore-manifest.json',journal)
                _restore_preserved_files(backup,item)
            else:
                # Older releases removed originals after external backup; retain
                # that journal format's restore behavior for existing backups.
                if destination.exists() and sha256(destination)==item['sha256']: destination.unlink()
                for old in item['older']:
                    if not Path(old['path']).exists(): copy_verified(checked_child(backup,old['backup']),old['path'],old['sha256'])
            item['status']='restored'; atomic_json(backup/'restore-manifest.json',journal)
            if progress: progress(i,len(selected),item['title'])
        journal['status']='restored' if all(e['status'] in ('restored','pending') for e in journal['entries']) else 'partially-restored'
        atomic_json(backup/'restore-manifest.json',journal)
    return len(selected)
