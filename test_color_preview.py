import copy,json,tempfile,unittest,tkinter as tk
from pathlib import Path
from unittest.mock import patch
from PIL import Image,ImageDraw
import marquee as m
import app as ui
from color_picker import ColorPicker,hex_color

class PreviewPerformanceTests(unittest.TestCase):
 def test_ui_version_does_not_force_rebuilding_unchanged_exports(self):
  game=m.Game('Test','Test','id'); profile={'main':'#112233'}
  signature=m.fingerprint(game,profile,[])
  with patch.object(m,'VERSION','99.0'): self.assertEqual(m.fingerprint(game,profile,[]),signature)
  with patch.object(m,'RENDER_VERSION','changed-artwork'): self.assertNotEqual(m.fingerprint(game,profile,[]),signature)
 def test_layer_cache_preserves_pixels_across_color_and_crop_changes(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
   base=Path(temporary); path=base/'source.png'
   source=Image.new('RGBA',(1100,700),'#4499CC'); ImageDraw.Draw(source).rectangle((200,200,800,600),fill='#F8DA61'); source.save(path)
   profile=copy.deepcopy(m.load_profiles()['default']); game=m.Game('Example','Test','id')
   selected={field:path for field in ['Game clear logo','Game box 3D','Game box front','Game box back','Game cart front','Fan art','Platform clear logo','Console image']}
   cache=m.PreviewLayerCache(); raw={}
   with patch.object(m,'add_effect',wraps=m.add_effect) as effects:
    first=m.render(None,game,profile,selected,image_cache=raw,layer_cache=cache); initial_calls=effects.call_count
    profile.update(main='#AABBCC',page_color='#AABBCC',page_back_color='#607080',background_position=70)
    updated=m.render(None,game,profile,selected,image_cache=raw,layer_cache=cache)
    self.assertLess(effects.call_count-initial_calls,initial_calls)
   expected=m.render(None,game,profile,selected,image_cache=raw)
   self.assertEqual(updated.tobytes(),expected.tobytes()); self.assertNotEqual(first.tobytes(),updated.tobytes())
   profile.update(main='#111111',preserve_corner_art=True,background_position=0)
   self.assertEqual(m.render(None,game,profile,selected,image_cache=raw,layer_cache=cache).tobytes(),m.render(None,game,profile,selected,image_cache=raw).tobytes())
 def test_layer_cache_obeys_memory_limit_and_recency(self):
  cache=m.PreviewLayerCache(max_bytes=800); layer=Image.new('RGBA',(10,10),'red')
  cache.put('a',layer); cache.put('b',layer); cache.get('a'); cache.put('c',layer)
  self.assertIsNone(cache.get('b')); self.assertIsNotNone(cache.get('a')); self.assertLessEqual(cache.bytes,800)
  cache.put('large',Image.new('RGBA',(100,100))); self.assertIsNone(cache.get('large')); self.assertLessEqual(cache.bytes,800)
 def test_media_folder_and_platform_logo_lookups_are_reused(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
   root=Path(temporary); (root/'Data'/'Platforms').mkdir(parents=True); lib=m.Library(root)
   folder=root/'Images'/'Test'/'Clear Logo'; folder.mkdir(parents=True); Image.new('RGB',(4,4)).save(folder/'Example-01.png')
   index=m.MediaIndex(lib,'Test'); game=m.Game('Example','Test')
   with patch.object(lib,'media_folder',wraps=lib.media_folder) as folders,patch('logo_packs.find_logo',return_value=None) as logos:
    for _ in range(5):
     self.assertTrue(index.choose(game,['Clear Logo'])[0]); self.assertIsNone(index.platform_source('Clear Logo'))
    self.assertEqual(folders.call_count,1); self.assertEqual(logos.call_count,1)

class ColorPickerTests(unittest.TestCase):
 def test_hex_validation(self):
  self.assertEqual(hex_color(' aabbcc '),'#AABBCC'); self.assertEqual(hex_color('#0011FF'),'#0011FF')
  for value in ['#12','red','#GG2233','##AABBCC','11223344',None]:
   with self.assertRaises(ValueError): hex_color(value)
 def test_picker_rgb_hue_keyboard_apply_and_cancel(self):
  root=tk.Tk(); root.withdraw(); changed=[]; applied=[]; cancelled=[]; picker=None
  try:
   picker=ColorPicker(root,'#FF0000',changed.append,applied.append,lambda:cancelled.append(True))
   picker.rgb_vars[1].set('255'); self.assertEqual(picker.value,'#FFFF00')
   picker.rgb_vars[0].set('999'); self.assertFalse(picker.valid); picker.apply(); self.assertFalse(picker.closed)
   picker.hex_var.set('00FF00'); self.assertEqual(picker.value,'#00FF00'); self.assertTrue(picker.valid)
   picker.hue_changed('240'); self.assertEqual(picker.value,'#0000FF')
   picker.area_key('Down'); self.assertNotEqual(picker.value,'#0000FF'); picker.apply()
   self.assertTrue(applied); self.assertFalse(cancelled); self.assertTrue(picker.closed)
   picker=ColorPicker(root,'#FFFFFF',changed.append,applied.append,lambda:cancelled.append(True)); picker.set_color('#111111'); picker.cancel()
   self.assertEqual(cancelled,[True]); self.assertEqual(len(applied),1)
  finally:
   if picker and not picker.closed: picker.cancel()
   root.update_idletasks(); root.destroy()
 def test_invalid_preferences_do_not_prevent_launch_and_close_flushes(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
   base=Path(temporary); settings=base/'settings.json'; settings.write_text('[]')
   root=tk.Tk(); root.withdraw(); instance=None
   try:
    with patch.object(ui,'HOME',base):
     instance=ui.App(root); root.update_idletasks(); instance.set_crop(67); instance.close(); instance=None
    self.assertEqual(json.loads(settings.read_text())['background_position'],67)
    self.assertFalse(settings.with_suffix('.tmp').exists())
   finally:
    if instance: instance.close()
    else:
     try: root.update_idletasks(); root.destroy()
     except tk.TclError: pass

if __name__=='__main__': unittest.main()
