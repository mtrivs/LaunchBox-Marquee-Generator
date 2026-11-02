"""Counted batch work, periodic recovery state, and verified export manifests."""
import json,os,time,uuid
from pathlib import Path
from PIL import Image
import marquee as m
from io_utils import OperationLock,read_json,sha256

REPORT=['platform','title','game_id','rom_path','output','status','missing','notes','sources','sha256']
EXCLUDED=['platform','title','game_id','reason','application_path']
COPY=['platform','export_folder','destination_folder','action']

def same_pixels(path,expected):
    """Verify a legacy or uncheckpointed PNG against the current full render.
    """
    try:
        with Image.open(path) as existing:
            return (existing.format=='PNG' and existing.size==m.SIZE and not getattr(existing,'is_animated',False)
                    and existing.getexif().get(274,1)==1 and existing.convert('RGB').tobytes()==expected.tobytes())
    except OSError: return False

def generate_batch(library,output,platforms,limit=0,title='',force=False,progress=None,cancelled=None,palette_overrides=None,
                   background_position=50,progress_state=None,checkpoint_games=25,logo_overrides=None):
    """Generate eligible games with counted progress and recoverable checkpoints.

    The legacy progress callback receives readable messages. progress_state
    receives independent dictionaries with phase, overall_done/overall_total,
    system_done/system_total, system_index/system_count, and platform.
    Every completed attempt counts, including unchanged files and errors.
    Hidden/non-game records and records beyond a limit do not count.

    An OS-held export lock prevents concurrent generators/installers from
    writing inconsistent state. checkpoint_games must be between 1 and 10000.
    """
    crop=m.crop_percentage(background_position)
    if not isinstance(checkpoint_games,int) or not 1<=checkpoint_games<=10000: raise ValueError('Checkpoint interval must be from 1 to 10000 games.')
    if limit<0: raise ValueError('The game limit cannot be negative.')
    output=m.ensure_output(library,output)
    with OperationLock(output):
        return _run(library,output,list(dict.fromkeys(platforms)),limit,title,force,progress,cancelled,palette_overrides,crop,progress_state,checkpoint_games,logo_overrides)

def _run(library,output,platforms,limit,title,force,progress,cancelled,palettes,crop,notify,interval,logos):
    """Prepare exact work totals, process systems, and publish recovery state.
    """
    state_file=output/'marquee-state.json'; state=read_json(state_file,{})
    previous=read_json(output/'marquee-checkpoint.json',{})
    if previous.get('library')==str(library.root.resolve()) and isinstance(previous.get('state'),dict): state.update(previous['state'])
    manifest=read_json(output/'marquee-manifest.json',{})
    inventory=manifest.get('images',{}) if isinstance(manifest.get('images'),dict) else {}
    rows=[]; excluded=[]; copy_rows=[]; plans=[]; profiles=m.load_profiles(); run_id=uuid.uuid4().hex
    total=0; done=0; last_write=time.monotonic(); completed_since_write=0
    info={'phase':'preparing','overall_done':0,'overall_total':0,'system_done':0,'system_total':0,'system_index':0,'system_count':len(platforms),'platform':'','game_title':''}
    def emit(**changes):
        """Send a copied progress snapshot so queued GUI events cannot be mutated.
        """
        info.update(changes)
        if notify: notify(dict(info))
    def checkpoint(phase=None):
        """Commit authoritative state first, followed by compatible reports.

        A crash between these atomic writes is recovered from the checkpoint.
        PNG files completed after the last checkpoint can be adopted only after
        an exact pixel comparison, never from their names or dimensions alone.
        """
        nonlocal last_write,completed_since_write
        if phase: info['phase']=phase
        snapshot={'version':1,'run_id':run_id,'library':str(library.root.resolve()),'state':state,'progress':dict(info)}
        # Checkpoint is the authoritative recovery snapshot; the compatible state
        # and user-readable reports are written atomically as projections of it.
        m.atomic_json(output/'marquee-checkpoint.json',snapshot)
        m.atomic_json(state_file,state)
        m.atomic_json(output/'marquee-manifest.json',{'version':1,'library':str(library.root.resolve()),'images':inventory})
        m.atomic_csv(output/'media-report.csv',rows,REPORT)
        m.atomic_csv(output/'excluded-games.csv',excluded,EXCLUDED)
        m.atomic_csv(output/'copy-map.csv',copy_rows,COPY)
        last_write=time.monotonic(); completed_since_write=0
    emit()
    for n,platform in enumerate(platforms,1):
        if cancelled and cancelled.is_set(): break
        if progress: progress(f'Counting visible games: {platform}...')
        all_games=sorted(library.games(platform),key=lambda g:(g.title.casefold(),g.id)); counts={}
        for game in all_games:
            name=m.media_name(game.title).casefold(); counts[name]=counts.get(name,0)+1
        games=[]
        for game in all_games:
            if title and title.casefold() not in game.title.casefold(): continue
            reason=m.exclusion_reason(game)
            if reason: excluded.append(dict(platform=platform,title=game.title,game_id=game.id,reason=reason,application_path=game.application))
            else: games.append(game)
        if limit: games=games[:limit]
        plans.append((platform,games,counts)); total+=len(games)
        emit(platform=platform,system_index=n)
    emit(phase='batch',overall_total=total,system_index=0,platform='')
    checkpoint()
    for n,(platform,games,counts) in enumerate(plans,1):
        if cancelled and cancelled.is_set(): break
        emit(platform=platform,system_index=n,system_done=0,system_total=len(games),game_title='')
        profile=dict(m.profile_for(library,platform,profiles))
        if palettes and platform in palettes: profile.update(palettes[platform])
        if logos and logos.get(platform) not in ('auto','',None): profile['logo_pack']=logos[platform]
        profile['background_position']=crop
        index=m.MediaIndex(library,platform)
        assets=[m.HOME/e[k] for e in profile['elements'] for k in ['Asset','FallbackAsset'] if e.get(k)]
        folder,destination_folder,mode=m.marquee_export_folder(library,platform,output); folder.mkdir(parents=True,exist_ok=True)
        copy_rows.append(dict(platform=platform,export_folder=str(folder.relative_to(output)),destination_folder=str(destination_folder),action=mode))
        seen=set()
        for i,game in enumerate(games,1):
            if cancelled and cancelled.is_set(): break
            # Publish the current game before discovery/rendering begins; the
            # counters below still advance only after that attempt completes.
            emit(game_title=game.title)
            record=dict(platform=platform,title=game.title,game_id=game.id,rom_path=str(library.resolve_path(game.application)) if game.application else '',output='',status='',missing='',notes='',sources='',sha256='')
            try:
                filename=m.marquee_filename(game,folder,counts[m.media_name(game.title).casefold()]>1)
                destination=folder/filename; record['output']=str(destination)
                if filename.casefold() in seen:
                    record.update(status='shared-title',notes='Same output name as another record; review before installation.')
                else:
                    seen.add(filename.casefold())
                    selected,sources,notes,missing=m.select_sources(library,game,profile,index)
                    signature=m.fingerprint(game,profile,list(sources.values())+assets)
                    key=destination.relative_to(output).as_posix(); known=inventory.get(key,{})
                    existing=destination.exists(); digest=sha256(destination) if existing else ''
                    verified=known.get('sha256')==digest and known.get('game_id')==game.id and known.get('platform')==platform and bool(digest)
                    if existing and not force and state.get(key)==signature and verified:
                        record['status']='unchanged'
                    elif existing and not force and state.get(key)==signature and known.get('signature')==signature and not verified:
                        record.update(status='changed-kept',notes='Export changed since it was verified; retained. Rebuild to replace it.')
                    # A manifest with an older signature can lag a durable
                    # checkpoint after a crash between projection writes. It
                    # needs pixel verification, not a false manual-edit flag.
                    elif existing and not force and (state.get(key)==signature or key not in state):
                        # Recover old exports/uncheckpointed PNGs only after an
                        # exact comparison against the current full renderer.
                        expected=m.render(library,game,profile,selected)
                        if same_pixels(destination,expected): record['status']='verified'; state[key]=signature; verified=True
                        else: record.update(status='existing-kept',notes='Existing image differs from the expected render; retained. Rebuild to replace it.')
                    else:
                        image=m.render(library,game,profile,selected); temporary=destination.with_suffix('.tmp')
                        # Flush the PNG before a later checkpoint attests to it.
                        with temporary.open('wb') as file:
                            image.save(file,format='PNG'); file.flush(); os.fsync(file.fileno())
                        temporary.replace(destination); state[key]=signature
                        digest=sha256(destination); record['status']='generated'; verified=True
                    if record['status'] in ('generated','verified','unchanged') and verified:
                        record['sha256']=digest
                        inventory[key]={'platform':platform,'title':game.title,'game_id':game.id,'sha256':digest,'size':list(m.SIZE),'signature':signature}
                    else: inventory.pop(key,None)
                    record['missing']='; '.join(missing); record['sources']=json.dumps(sources,ensure_ascii=False)
                    record['notes']='; '.join(filter(None,[record['notes']]+notes+index.warnings)); index.warnings.clear()
            except Exception as error:
                record.update(status='error',notes=str(error))
                if record['output']:
                    inventory.pop(Path(record['output']).relative_to(output).as_posix(),None)
            rows.append(record); done+=1; completed_since_write+=1
            emit(overall_done=done,system_done=i)
            if progress: progress(f'{platform}: {i}/{len(games)} | Overall {done}/{total} - {game.title} ({record["status"]})')
            if completed_since_write>=interval or time.monotonic()-last_write>=30: checkpoint()
        checkpoint()
    stopped=bool(cancelled and cancelled.is_set())
    emit(phase='stopped' if stopped else 'complete',overall_done=done)
    checkpoint()
    return rows
