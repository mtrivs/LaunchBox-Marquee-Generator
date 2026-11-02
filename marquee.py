"""LaunchBox marquee generator: read-only library discovery and batch compositing."""
from __future__ import annotations
import argparse, csv, hashlib, json, math, os, re, sys, time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path, PureWindowsPath
from image_cache import ImageCache
from io_utils import atomic_json,atomic_csv,read_json,sha256
from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageChops

HOME = Path(__file__).resolve().parent
SIZE = (1920, 360)
VERSION = '2.4.1'
# UI and cache improvements do not invalidate unchanged 1.9.1 exports.
RENDER_VERSION = '1.9.1'
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}
MEDIA = {
 'Game clear logo': ['Clear Logo'],
 'Game box 3D': ['Box - 3D', 'Box - Front', 'Box - Front - Reconstructed'],
 'Game box front': ['Box - Front', 'Box - Front - Reconstructed'],
 'Game box back': ['Box - Back', 'Box - Back - Reconstructed'],
 'Game cart front': ['Cart - Front', 'Cart - 3D', 'Disc', 'Fanart - Cart - Front'],
 'Fan art': ['Fanart - Background', 'Screenshot - Gameplay', 'Box - Front'],
}
REGIONS = {'us': ['us','United States','North America','USA'], 'eu': ['eu','Europe','United Kingdom'], 'jp':['jp','Japan'], 'au':['au','Australia']}


def safe_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).rstrip(' .') or 'Untitled'
    if re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', value):
        value = '_' + value
    return value[:160]


def media_title(value):
    """LaunchBox title matching also replaces apostrophes and collapses runs."""
    return re.sub(r"[<>:\"/\\|?*'\x00-\x1f]+", '_', value).rstrip(' .') or 'Untitled'


def media_name(value):
    # Folder names use safe_name separately: apostrophes are legal in folders.
    return safe_name(media_title(value))


def key(value):
    return re.sub(r'[^\w]', '', value.casefold()).replace('_','')


def image_stem(path):
    return re.sub(r'-\d{2,}$', '', path.stem)


def root_folder(value):
    path = Path(value).expanduser().resolve()
    for candidate in [path, path / 'LaunchBox']:
        if (candidate / 'Data' / 'Platforms').is_dir():
            return candidate
    raise ValueError('Choose the LaunchBox folder containing Data and Images, or its parent.')


@dataclass
class Game:
    title: str
    platform: str
    id: str = ''
    database_id: str = ''
    application: str = ''
    region: str = ''
    hidden: bool = False
    version: str = ''

    def aliases(self):
        rom = PureWindowsPath(self.application).stem
        titles = list(dict.fromkeys([media_name(self.title), self.title, safe_name(self.title)]))
        specific = [name+'.'+self.id for name in titles] if self.id else []
        # Canonical LaunchBox names come first; keep older generated filenames
        # as source aliases so existing scraped media remains usable.
        return list(dict.fromkeys(filter(None, specific+titles+[rom, re.sub(r'\s*[\[(].*', '', rom)])))


def marquee_filename(game, folder, duplicate=False):
    """Keep title matching where possible, GUID matching when names must change."""
    canonical=media_title(game.title)
    stem=media_name(game.title)
    identity=''
    if duplicate or stem!=canonical:
        identity='.'+(safe_name(game.id) if game.id else hashlib.sha256(game.application.encode()).hexdigest()[:16])
    # A normal Windows path must remain below MAX_PATH, including the temporary
    # save name. Retain the complete GUID rather than truncating its identity.
    budget=248-len(str(folder.resolve()))-1-len('-01.png')
    if len(stem)+len(identity)>budget and game.id:
        identity='.'+safe_name(game.id)
        room=budget-len(identity)
        if room<1:
            raise ValueError('The marquee output path is too long. Choose a shorter output folder.')
        stem=stem[:room].rstrip(' .') or '_'
    return stem+identity+'-01.png'


def exclusion_reason(game):
    """Use LaunchBox visibility and explicit non-game markers, never loose keywords."""
    if game.hidden:
        return 'Hidden in LaunchBox'
    if re.match(r'(?i)^\s*ZZZ\s*\(notgame\)', game.title):
        return 'Explicit notgame entry'
    # Check the filename, not parent folders, so a folder called [DLC] does not
    # accidentally exclude an otherwise ordinary game. Plain "DLC Quest" is valid.
    for value in (game.title, game.version, PureWindowsPath(game.application).name):
        match = re.search(r'(?i)(?:\[\s*(BIOS|Device|DLC|Update)\s*\]|\(\s*(BIOS|Device|DLC|Update)\s*\))', value)
        if match:
            marker = next(group for group in match.groups() if group).upper()
            return f'Explicit {marker} marker'
    return ''


class Library:
    def __init__(self, path):
        self.root = root_folder(path)
        self.platform_files = {p.stem:p for p in sorted((self.root/'Data'/'Platforms').glob('*.xml')) if not re.search(r'(?i)(\.backup|\.bak| - backup)',p.stem)}
        self.folders, self.scrape_as = {}, {}
        file = self.root/'Data'/'Platforms.xml'
        if file.is_file():
            doc = ET.parse(file).getroot()
            for item in doc.findall('PlatformFolder'):
                self.folders[(item.findtext('Platform',''),item.findtext('MediaType',''))] = item.findtext('FolderPath','')
            for item in doc.findall('Platform'):
                self.scrape_as[item.findtext('Name','')] = item.findtext('ScrapeAs','')

    def games(self, platform):
        games = []
        for _, item in ET.iterparse(self.platform_files[platform], events=['end']):
            if item.tag == 'Game':
                games.append(Game(item.findtext('Title','Untitled'), item.findtext('Platform',platform) or platform, item.findtext('ID',''),item.findtext('DatabaseID',''),item.findtext('ApplicationPath',''),item.findtext('Region',''),(item.findtext('Hide','') or '').strip().casefold() in {'true','1'},item.findtext('Version','')))
                item.clear()
        return games

    def resolve_path(self, value, default=None):
        if not value:
            return default
        candidate = Path(os.path.expandvars(value))
        if not candidate.is_absolute():
            return self.root/candidate
        if candidate.exists():
            return candidate
        parts = list(PureWindowsPath(value).parts)
        for i, part in enumerate(parts):
            if part.casefold() == 'launchbox' and i+1<len(parts):
                moved = self.root.joinpath(*parts[i+1:])
                if moved.exists():
                    return moved
        return candidate

    def configured_folder(self,platform,kind):
        return next((value for (name,media),value in self.folders.items() if name.casefold()==platform.casefold() and media.casefold()==kind.casefold()),'')

    def media_folder(self, platform, kind):
        default = self.root/'Images'/safe_name(platform)/kind
        configured = self.resolve_path(self.configured_folder(platform,kind),default)
        return configured if configured and configured.is_dir() else default


class MediaIndex:
    def __init__(self, library, platform):
        self.library,self.platform = library,platform
        self.indexes,self.warnings = {},[]
        self.folder_paths={}; self.platform_paths={}

    def folder(self,kind):
        if kind not in self.folder_paths: self.folder_paths[kind]=self.library.media_folder(self.platform,kind)
        return self.folder_paths[kind]

    def platform_source(self,kind,platform=None,logo_pack='auto'):
        platform=platform or self.platform; lookup=(platform,kind,logo_pack)
        if lookup not in self.platform_paths:
            if kind=='Clear Logo':
                from logo_packs import find_logo
                self.platform_paths[lookup]=find_logo(self.library,platform,logo_pack)
            else: self.platform_paths[lookup]=platform_media(self.library,platform,kind)
        return self.platform_paths[lookup]

    def index(self, kind):
        if kind not in self.indexes:
            exact, normalized = {},{}
            folder = self.folder(kind)
            if folder.is_dir():
                for parent,dirs,files in os.walk(folder):
                    dirs.sort(key=str.casefold)
                    for filename in sorted(files,key=str.casefold):
                        path = Path(parent)/filename
                        if path.suffix.lower() in EXTENSIONS:
                            stem = image_stem(path)
                            exact.setdefault(stem.casefold(),[]).append(path)
                            normalized.setdefault(key(stem),[]).append(path)
            self.indexes[kind] = exact,normalized
        return self.indexes[kind]

    def choose(self, game, kinds):
        for kind in kinds:
            exact,normalized = self.index(kind)
            candidates=[]
            for alias in game.aliases()+[game.id,game.database_id]:
                if alias and alias not in {'0','-1'}:
                    candidates = exact.get(alias.casefold(),[])
                    if candidates: break
            if not candidates:
                for alias in game.aliases():
                    matched = normalized.get(key(alias),[])
                    distinct = {image_stem(p).casefold() for p in matched}
                    if len(distinct)==1:
                        candidates=matched
                        break
                    if len(distinct)>1:
                        self.warnings.append(f'Ambiguous {kind} match: {game.title}')
            if candidates:
                regions=[game.region]
                for region in re.split('[,;]',game.region):
                    region=region.strip()
                    regions+=REGIONS.get(region.casefold(),[region])
                regions+=['North America','United States','us','World','Europe','eu','Japan','jp']
                order=list(dict.fromkeys(r.casefold() for r in regions if r))
                media_root=self.folder(kind)
                def score(path):
                    rel=path.relative_to(media_root)
                    region=rel.parts[0].casefold() if len(rel.parts)>1 else ''
                    return (order.index(region) if region in order else len(order)+(1 if region else 0),str(rel).casefold())
                return sorted(candidates,key=score)[0],kind
        return None,''


def load_profiles():
    profiles=json.loads((HOME/'profiles.json').read_text(encoding='utf-8'))
    layout=profiles['layout']
    for profile in [profiles['default']]+list(profiles['platforms'].values()):
        for element in profile['elements']:
            # Geometry comes exclusively from the shared layout, never a platform.
            element.update(layout[element['Tag']])
    return profiles


def profile_for(library, platform, profiles):
    lookup={k.casefold():v for k,v in profiles['platforms'].items()}
    for name in [platform,library.scrape_as.get(platform,'')]:
        if name.casefold() in lookup: return lookup[name.casefold()]
    return profiles['default']


def color(value):
    if value.startswith('#') and len(value)==9:
        return tuple(int(value[i:i+2],16) for i in (3,5,7,1))
    return ImageColor.getcolor(value,'RGBA')


DECODED_CACHE=ImageCache()


def decode_image(filename):
    with Image.open(filename) as source:
        return ImageOps.exif_transpose(source).convert('RGBA')


def read_image(filename,stamp):
    cache_key=(filename,stamp)
    try: return DECODED_CACHE[cache_key]
    except KeyError:
        image=decode_image(filename); DECODED_CACHE[cache_key]=image; return image


def open_image(path,use_cache=True):
    if not use_cache: return decode_image(path)
    stat=path.stat()
    return read_image(str(path),(stat.st_mtime_ns,stat.st_size)).copy()


def trim_transparency(source):
    # Ignore very faint alpha fringe when measuring visible artwork, not white areas.
    alpha=source.getchannel('A')
    bounds=alpha.point(lambda value:255 if value>8 else 0).getbbox()
    return source.crop(bounds) if bounds else Image.new('RGBA',(1,1))


def crop_percentage(value):
    try: percentage=float(value)
    except (TypeError,ValueError): raise ValueError('Background crop must be a number from 0 to 100.')
    if not math.isfinite(percentage) or not 0<=percentage<=100:
        raise ValueError('Background crop must be between 0 and 100.')
    return percentage


def fit_image(source,width,height,stretch='Uniform',horizontal='Center',vertical='Center',trim=False,vertical_position=None):
    if trim: source=trim_transparency(source)
    if stretch=='Fill': return source.resize((width,height),Image.Resampling.LANCZOS)
    scale=(max if stretch=='UniformToFill' else min)(width/source.width,height/source.height)
    fitted=source.resize((max(1,round(source.width*scale)),max(1,round(source.height*scale))),Image.Resampling.LANCZOS)
    if trim: fitted=trim_transparency(fitted)
    x={'Left':0,'Center':(width-fitted.width)//2,'Right':width-fitted.width}.get(horizontal,0)
    y=round((height-fitted.height)*crop_percentage(vertical_position)/100) if vertical_position is not None else {'Top':0,'Center':(height-fitted.height)//2,'Bottom':height-fitted.height}.get(vertical,0)
    canvas=Image.new('RGBA',(width,height))
    canvas.alpha_composite(fitted,(x,y))
    return canvas


def tint(source,value):
    result=ImageChops.multiply(source,Image.new('RGBA',source.size,color(value)))
    result.putalpha(source.getchannel('A'))
    return result


def page_color_for(profile):
    return profile.get('page_color') or profile['main']


def shade_color(value,factor=.68):
    return '#'+''.join(f'{round(channel*factor):02X}' for channel in color(value)[:3])


def page_back_color_for(profile):
    # A legacy red accent must not leak into the reverse of a neutral/blue fold.
    return profile.get('page_back_color') or shade_color(page_color_for(profile))


def luminance(value):
    channels=[channel/255 for channel in color(value)[:3]]
    linear=[channel/12.92 if channel<=.04045 else ((channel+.055)/1.055)**2.4 for channel in channels]
    return sum(a*b for a,b in zip(linear,[.2126,.7152,.0722]))


def contrast_ratio(first,second):
    low,high=sorted([luminance(first),luminance(second)])
    return (high+.05)/(low+.05)


def logo_outline_color(profile):
    return max(['#000000','#FFFFFF'],key=lambda candidate:contrast_ratio(candidate,profile['main']))


def readable_logo(layer,profile):
    alpha=layer.getchannel('A')
    outline_alpha=ImageChops.subtract(alpha.filter(ImageFilter.MaxFilter(3)),alpha)
    outline=Image.new('RGBA',SIZE,color(logo_outline_color(profile)))
    outline.putalpha(outline_alpha)
    outline.alpha_composite(layer)
    return outline


def colorize_page(source,value):
    target=color(value)
    shading=ImageOps.grayscale(source)
    result=ImageOps.colorize(shading,tuple(round(c*0.12) for c in target[:3]),target[:3]).convert('RGBA')
    result.putalpha(source.getchannel('A'))
    return result


def add_effect(layer,element):
    effect=element.get('EffectType','None')
    radius=float(element.get('EffectBlurRadius',0))/3
    if effect=='Blur': return layer.filter(ImageFilter.GaussianBlur(radius))
    if effect=='Drop Shadow':
        strength=float(element.get('EffectOpacity',1))
        alpha=layer.getchannel('A').filter(ImageFilter.GaussianBlur(radius)).point(lambda a:round(a*strength))
        shadow=Image.new('RGBA',SIZE,color(element.get('EffectColor','Black')))
        shadow.putalpha(alpha)
        depth=float(element.get('EffectShadowDepth',0))
        angle=math.radians(float(element.get('EffectDirection',0)))
        out=Image.new('RGBA',SIZE)
        out.alpha_composite(shadow,(round(math.cos(angle)*depth),round(-math.sin(angle)*depth)))
        out.alpha_composite(layer)
        return out
    return layer


def platform_media(library,platform,kind):
    folder=library.root/'Images'/'Platforms'/safe_name(platform)/kind
    if folder.is_dir():
        return next((p for p in sorted(folder.iterdir()) if p.suffix.lower() in EXTENSIONS),None)
    return None


def text_logo(title):
    canvas=Image.new('RGBA',(848,325))
    draw=ImageDraw.Draw(canvas)
    path=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/'arialbd.ttf'
    for size in range(95,15,-2):
        font=ImageFont.truetype(str(path),size) if path.is_file() else ImageFont.load_default()
        if draw.textbbox((0,0),title,font=font)[2]<=820: break
    draw.text((424,162),title,fill='white',font=font,anchor='mm',stroke_width=1)
    return canvas


def select_sources(library,game,profile,index):
    selected,sources,notes,missing={},{},[],[]
    for field,kinds in MEDIA.items():
        path,kind=index.choose(game,kinds)
        selected[field]=path
        if path:
            sources[field]=str(path)
            if kind!=kinds[0]: notes.append(f'{field}: used {kind}')
    selected['Platform clear logo']=index.platform_source('Clear Logo',game.platform,profile.get('logo_pack','auto'))
    device_asset=next((e.get('Asset') for e in profile['elements'] if e.get('Tag')=='Console Image' and e.get('Asset')),None)
    selected['Console image']=(HOME/device_asset) if device_asset else index.platform_source('Device',game.platform)
    for field in ['Platform clear logo','Console image']:
        if selected[field]: sources[field]=str(selected[field])
    for field in ['Game clear logo','Game box 3D','Game cart front','Fan art']:
        if not selected[field]:
            missing.append(field)
            fallback=next((e.get('FallbackAsset') for e in profile['elements'] if e.get('MetadataImageField')==field and e.get('FallbackAsset')),None)
            if fallback:
                sources['Template fallback: '+field]=str(HOME/fallback)
                notes.append('Template fallback used for '+field)
    if not selected['Console image'] and not any(e.get('Asset') for e in profile['elements'] if e.get('Tag')=='Console Image'):
        missing.append('Console image')
    return selected,sources,notes,missing


def position_layer(source,element):
    width,height=round(float(element['W'])),round(float(element['H']))
    x,y=float(element.get('X',0)),float(element.get('Y',0))
    angle=float(element.get('Angle',0))
    if angle: source=source.rotate(-angle,Image.Resampling.BICUBIC,expand=True)
    layer=Image.new('RGBA',SIZE)
    layer.alpha_composite(source,(round(x+(width-source.width)/2),round(y+(height-source.height)/2)))
    return layer


@lru_cache(maxsize=8)
def _paper_mask(geometry,inset):
    x,y,width,height,angle=geometry
    element=dict(X=x,Y=y,W=width,H=height,Angle=angle)
    paper=position_layer(Image.new('RGBA',(round(float(width)),round(float(height))),'white'),element)
    mask=paper.getchannel('A').point(lambda a:255 if a>=250 else 0)
    if inset:
        mask=mask.filter(ImageFilter.MinFilter(inset*2+1))
        ImageDraw.Draw(mask).rectangle((0,0,SIZE[0]-1,SIZE[1]-1),outline=0,width=inset)
    return mask


def corner_paper_mask(profile,inset=8):
    element=next(e for e in profile['elements'] if e['Tag']=='Page curl background')
    geometry=tuple(element.get(k,0) for k in ['X','Y','W','H','Angle'])
    return _paper_mask(geometry,inset)



class PreviewLayerCache:
    """Byte-bounded LRU of completed preview layers; batch output stays uncached."""
    def __init__(self,max_bytes=64*1024*1024):
        self.max_bytes=max_bytes; self.bytes=0; self.layers=OrderedDict()
    def get(self,key):
        item=self.layers.get(key)
        if item is None: return None
        self.layers.move_to_end(key); return item
    def put(self,key,layer):
        size=layer.width*layer.height*len(layer.getbands())
        old=self.layers.pop(key,None)
        if old is not None: self.bytes-=old.width*old.height*len(old.getbands())
        if size>self.max_bytes: return
        while self.layers and self.bytes+size>self.max_bytes:
            _,removed=self.layers.popitem(last=False)
            self.bytes-=removed.width*removed.height*len(removed.getbands())
        self.layers[key]=layer; self.bytes+=size


def preview_layer_key(element,game,profile,selected):
    tag=element.get('Tag',''); dynamic=[]
    if tag=='Page curl background' or (element.get('CornerSliver') and not profile.get('preserve_corner_art')):
        dynamic.append(profile['main'])
    if element.get('CornerSliver'): dynamic.append(bool(profile.get('preserve_corner_art')))
    if tag=='Page Curl': dynamic.append(page_color_for(profile))
    if tag=='Page curl shadow': dynamic.append(page_back_color_for(profile))
    if tag=='Fan Art': dynamic.append(profile.get('background_position',50))
    if tag=='Platform Clear Art':
        paper=next(e for e in profile['elements'] if e['Tag']=='Page curl background')
        dynamic.extend([logo_outline_color(profile),tuple(paper.get(k,0) for k in ['X','Y','W','H','Angle'])])
    static=(element.get('UIType')=='Rectangle' or
            (element.get('UIType')=='Image' and element.get('ImageValueType')!='Metadata' and
             (element.get('Asset') or tag!='Console Image')))
    if static:
        # Template layers do not depend on the randomly selected game. Reuse
        # them across contexts; local asset timestamps invalidate edited files.
        path=HOME/element['Asset'] if element.get('Asset') else None
        stamp=None
        if path and path.is_file():
            stat=path.stat(); stamp=(stat.st_mtime_ns,stat.st_size)
        return ('template',json.dumps(element,sort_keys=True),stamp,tuple(dynamic))
    return (json.dumps(element,sort_keys=True),game.title,game.platform,
            tuple(sorted((field,str(path)) for field,path in selected.items())),tuple(dynamic))


def render(library,game,profile,selected,image_cache=None,layer_cache=None,cache_namespace=None):
    """Composite the full-resolution marquee without changing source buffers.

    GUI callers supply a fresh cache namespace per loaded game/logo context.
    Only game-independent template layers may then survive context changes.
    Batch callers omit caches and retain the established rendering path.
    """
    canvas=Image.new('RGBA',SIZE,'black')
    for element in sorted(profile['elements'],key=lambda e:float(e.get('Zorder',0))):
        if element.get('IsVisible')=='False' or element.get('Visibility') in ['Hidden','Collapsed']: continue
        tag=element.get('Tag','')
        if layer_cache is not None:
            layer_key=preview_layer_key(element,game,profile,selected)
            # Game media is scoped to its immutable preview context so a later
            # reload cannot reuse stale layers after an external artwork edit.
            if cache_namespace is not None and layer_key[0]!='template': layer_key=(cache_namespace,layer_key)
            cached_layer=layer_cache.get(layer_key)
            if cached_layer is not None:
                canvas.alpha_composite(cached_layer); continue
        width,height=max(1,round(float(element.get('W',1)))),max(1,round(float(element.get('H',1))))
        x,y=float(element.get('X',0)),float(element.get('Y',0))
        if element.get('UIType')=='Rectangle':
            value=profile['main'] if tag=='Page curl background' else element.get('ForeColor','Black')
            source=Image.new('RGBA',(width,height),color(value))
        elif element.get('UIType')=='Image':
            field=element.get('MetadataImageField','')
            path=selected.get(field) if element.get('ImageValueType')=='Metadata' else None
            if tag=='Console Image' and not element.get('Asset') and selected.get('Console image'): path=selected['Console image']
            if not path:
                asset=element.get('FallbackAsset') if element.get('ImageValueType')=='Metadata' else element.get('Asset')
                path=HOME/asset if asset else None
            cache_key=str(path) if path else ''
            if path and ((image_cache is not None and cache_key in image_cache) or path.is_file()):
                if image_cache is None: source=open_image(path)
                else:
                    try: source=image_cache[cache_key].copy()
                    except KeyError:
                        # Templates recur for every random game. Share their immutable
                        # decoded buffers across contexts, within the existing byte cap.
                        if path.is_relative_to(HOME):
                            stat=path.stat()
                            source=read_image(str(path),(stat.st_mtime_ns,stat.st_size))
                        else: source=open_image(path,use_cache=False)
                        image_cache[cache_key]=source
                        source=source.copy()
            elif field=='Game clear logo': source=text_logo(game.title)
            elif field=='Platform clear logo': source=text_logo(game.platform)
            else: continue
            if element.get('TrimTransparency'):
                source=trim_transparency(source)
            if element.get('CornerSliver') and not profile.get('preserve_corner_art'):
                source=colorize_page(source,profile['main'])
            if tag=='Page Curl':
                # Shared palette behavior replaces the imported template's tint switch.
                source=colorize_page(source,page_color_for(profile))
            elif tag=='Page curl shadow':
                source=colorize_page(source,page_back_color_for(profile))
            elif element.get('UseColorTint')=='True' and tag!='Platform Clear Art':
                source=tint(source,element.get('ColorTint','White'))
            source=fit_image(source,width,height,element.get('Stretch','Uniform'),element.get('HorizontalAlignment','Center'),element.get('VerticalAlignment','Center'),trim=element.get('TrimTransparency',False),vertical_position=profile.get('background_position',50) if tag=='Fan Art' else None)
        else: continue
        opacity=float(element.get('Opacity',1))
        if opacity!=1: source.putalpha(source.getchannel('A').point(lambda a:round(a*opacity)))
        layer=position_layer(source,element)
        if tag=='Platform Clear Art':
            layer=readable_logo(layer,profile)
        layer=add_effect(layer,element)
        if tag=='Platform Clear Art':
            # Constrain both the logo and its shadow to the actual paper silhouette.
            layer.putalpha(ImageChops.multiply(layer.getchannel('A'),corner_paper_mask(profile)))
        if layer_cache is not None: layer_cache.put(layer_key,layer)
        canvas.alpha_composite(layer)
    return canvas.convert('RGB')


def fingerprint(game,profile,paths):
    inputs=[]
    for path in sorted(set(str(p) for p in paths if p)):
        stat=Path(path).stat()
        inputs.append((path,stat.st_size,stat.st_mtime_ns))
    return hashlib.sha256(json.dumps([RENDER_VERSION,game.__dict__,profile,inputs],sort_keys=True).encode()).hexdigest()


def ensure_output(library,output):
    output=Path(output).resolve()
    source=library.root.resolve()
    if output==source or source in output.parents: raise ValueError('Choose an output folder outside LaunchBox to keep original media safe.')
    output.mkdir(parents=True,exist_ok=True)
    return output


def marquee_export_folder(library,platform,output):
    """Return copy-ready staging and real destination, without writing into LaunchBox."""
    configured=library.configured_folder(platform,'Arcade - Marquee')
    raw=Path(os.path.expandvars(configured)) if configured else Path('Images')/safe_name(platform)/'Arcade - Marquee'
    destination=(library.root/raw).resolve() if not raw.is_absolute() else raw.resolve()
    if raw.is_absolute() and not destination.exists():
        # Relocated portable installs can retain the former drive in folder overrides.
        parts=list(PureWindowsPath(str(raw)).parts)
        for i,part in enumerate(parts):
            if part.casefold()=='launchbox' and i+1<len(parts):
                destination=library.root.joinpath(*parts[i+1:]).resolve()
                break
    try:
        relative=destination.relative_to(library.root.resolve())
        mode='Merge this relative folder into LaunchBox'
    except ValueError:
        relative=Path('External marquee folders')/safe_name(platform)/safe_name(destination.name or 'Arcade - Marquee')
        mode='Copy this system folder to the configured external destination'
    if not relative.parts:
        raise ValueError('Marquee destination cannot be the LaunchBox root.')
    staging=(Path(output)/relative).resolve()
    if Path(output).resolve() not in staging.parents:
        raise ValueError('The output path must stay inside the chosen output folder.')
    return staging,destination,mode


def generate(library,output,platforms,limit=0,title='',force=False,progress=None,cancelled=None,palette_overrides=None,background_position=50,progress_state=None,checkpoint_games=25,logo_overrides=None):
    from batch_jobs import generate_batch
    return generate_batch(library,output,platforms,limit,title,force,progress,cancelled,palette_overrides,background_position,progress_state,checkpoint_games,logo_overrides)


def main():
    parser=argparse.ArgumentParser(description='Build 1920 x 360 marquees using existing LaunchBox media.')
    parser.add_argument('--launchbox',help='LaunchBox installation folder')
    parser.add_argument('--output',help='Separate destination folder')
    parser.add_argument('--platform',action='append',help='Repeat to select multiple systems')
    parser.add_argument('--limit',type=int,default=0,help='Maximum games per system; 0 means all')
    parser.add_argument('--title',default='',help='Match part of a game title')
    parser.add_argument('--background-position',type=float,default=50,help='Vertical background crop: 0 top, 50 center, 100 bottom')
    parser.add_argument('--checkpoint-games',type=int,default=25,help='Checkpoint every N completed games (1-10000)')
    parser.add_argument('--image-cache-mb',type=int,default=128,help='Decoded source image cache in MiB (16-1024)')
    parser.add_argument('--logo-pack',default='auto',help='Installed platform logo pack name, or auto')
    parser.add_argument('--force',action='store_true',help='Rebuild existing images in the output folder')
    parser.add_argument('--list',action='store_true',help='List detected systems')
    args=parser.parse_args()
    if not args.launchbox:
        from app import run
        run()
        return
    library=Library(args.launchbox)
    if args.list:
        print('\n'.join(library.platform_files))
        return
    if not args.output: parser.error('--output is required for rendering')
    if args.limit<0: parser.error('--limit must be zero or positive')
    platforms=args.platform or list(library.platform_files)
    unknown=set(platforms)-set(library.platform_files)
    if unknown: parser.error('Unknown systems: '+', '.join(sorted(unknown)))
    if not 16<=args.image_cache_mb<=1024: parser.error('--image-cache-mb must be from 16 to 1024')
    DECODED_CACHE.set_budget(args.image_cache_mb*1024*1024)
    rows=generate(library,args.output,platforms,args.limit,args.title,args.force,print,background_position=args.background_position,checkpoint_games=args.checkpoint_games,logo_overrides={p:args.logo_pack for p in platforms})
    failures=sum(r['status']=='error' for r in rows)
    print(f'Finished: {len(rows)} records, {failures} errors. See media-report.csv and excluded-games.csv.')
    if failures: sys.exit(1)

if __name__=='__main__':
    try:
        main()
    except Exception as error:
        import traceback
        details=traceback.format_exc()
        (HOME/'error.log').write_text(details,encoding='utf-8')
        if '--launchbox' not in sys.argv:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0,str(error)+'\n\nDetails were saved in error.log beside the program.','Marquee Generator',0x10)
        else:
            print(details,file=sys.stderr)
        sys.exit(1)


