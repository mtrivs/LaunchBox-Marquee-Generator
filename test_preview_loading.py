"""Regression checks for new-game discovery and shared template reuse."""
import copy,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import marquee as m
from live_preview import PreviewMediaIndex
from image_cache import ImageCache
from logo_packs import find_logo

class PreviewLoadingTests(unittest.TestCase):
 def test_existing_logo_does_not_enumerate_unused_packs(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
   root=Path(temporary); (root/'Data'/'Platforms').mkdir(parents=True); library=m.Library(root)
   logo=root/'Images'/'Platforms'/'Test'/'Clear Logo'/'console.png'; logo.parent.mkdir(parents=True); Image.new('RGBA',(20,20),'red').save(logo)
   with patch('logo_packs.packs',side_effect=AssertionError('Unused packs should not be scanned')):
    self.assertEqual(find_logo(library,'Test'),logo)
 def test_console_and_folder_discovery_is_reused_across_random_games(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
   root=Path(temporary); (root/'Data'/'Platforms').mkdir(parents=True); library=m.Library(root); shared={}
   with patch.object(library,'media_folder',wraps=library.media_folder) as folders,patch('logo_packs.find_logo',return_value=None) as logos:
    for title in ['Alpha','Beta','Gamma']:
     index=PreviewMediaIndex(library,'Test',m.Game(title,'Test'),shared)
     index.folder('Clear Logo'); index.platform_source('Clear Logo')
    self.assertEqual(folders.call_count,1); self.assertEqual(logos.call_count,1)
    index.platform_source('Clear Logo',logo_pack='Custom'); self.assertEqual(logos.call_count,2)
    fresh=PreviewMediaIndex(library,'Test',m.Game('Alpha','Test'),{})
    fresh.folder('Clear Logo'); fresh.platform_source('Clear Logo')
    self.assertEqual(folders.call_count,2); self.assertEqual(logos.call_count,3)
 def test_template_layers_reuse_pixels_while_new_game_media_stays_fresh(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
   home=Path(temporary); asset=home/'template.png'; source=home/'game.png'
   Image.new('RGBA',(80,40),'red').save(asset); Image.new('RGBA',(80,40),'blue').save(source)
   profile={'main':'#112233','elements':[
    {'Tag':'Fan Art','UIType':'Image','ImageValueType':'Metadata','MetadataImageField':'Fan art','W':'1920','H':'360','Zorder':'0'},
    {'Tag':'Console Image','UIType':'Image','Asset':'template.png','X':'1500','Y':'50','W':'300','H':'200','Zorder':'1'}]}
   layers=m.PreviewLayerCache(); selected={'Fan art':source}
   with patch.object(m,'HOME',home),patch.object(m,'add_effect',wraps=m.add_effect) as effects:
    m.render(None,m.Game('Alpha','Test'),profile,selected,image_cache=ImageCache(),layer_cache=layers,cache_namespace=object()); before=effects.call_count
    result=m.render(None,m.Game('Beta','Other'),profile,selected,image_cache=ImageCache(),layer_cache=layers,cache_namespace=object())
    self.assertEqual(effects.call_count-before,1,'Only the game-dependent layer should be rebuilt')
    self.assertEqual(result.tobytes(),m.render(None,m.Game('Beta','Other'),profile,selected).tobytes())
    Image.new('RGBA',(81,40),'green').save(source)
    updated=m.render(None,m.Game('Alpha','Test'),profile,selected,image_cache=ImageCache(),layer_cache=layers,cache_namespace=object())
    self.assertNotEqual(result.tobytes(),updated.tobytes()); self.assertEqual(updated.getpixel((960,180)),(0,128,0))
    Image.new('RGBA',(81,40),'yellow').save(asset)
    changed=m.render(None,m.Game('Alpha','Test'),profile,selected,image_cache=ImageCache(),layer_cache=layers,cache_namespace=object())
    self.assertNotEqual(changed.tobytes(),updated.tobytes()); self.assertEqual(changed.getpixel((1650,150)),(255,255,0))
    self.assertLessEqual(layers.bytes,layers.max_bytes)
 def test_decoded_templates_are_shared_without_modifying_cached_pixels(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
   home=Path(temporary); asset=home/'template.png'; Image.new('RGBA',(80,40),'red').save(asset)
   profile={'main':'#112233','elements':[{'Tag':'Console Image','UIType':'Image','Asset':'template.png','W':'300','H':'200','Opacity':'.5'}]}
   with patch.object(m,'HOME',home),patch.object(m,'decode_image',wraps=m.decode_image) as decode:
    for title in ['Alpha','Beta']:
     m.render(None,m.Game(title,'Test'),profile,{},image_cache=ImageCache())
    self.assertEqual(decode.call_count,1)
    stat=asset.stat(); shared=m.read_image(str(asset),(stat.st_mtime_ns,stat.st_size))
    self.assertEqual(shared.getpixel((0,0)),(255,0,0,255))

if __name__=='__main__': unittest.main()
