import copy,json,tempfile,time,unittest,threading
from pathlib import Path
from unittest.mock import patch
import tkinter as tk
from PIL import Image,ImageDraw
import marquee as m
import app as ui

class LiveAppTests(unittest.TestCase):
 def test_live_updates_hold_game_reroll_and_persist_crop(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
   base=Path(temporary); library_root=base/'LaunchBox'; data=library_root/'Data'/'Platforms'; data.mkdir(parents=True)
   data.joinpath('Test.xml').write_text('<LaunchBox><Game><Title>Alpha</Title><ID>a</ID><Platform>Test</Platform></Game><Game><Title>Beta</Title><ID>b</ID><Platform>Test</Platform></Game><Game><Title>Hidden</Title><ID>hidden</ID><Hide>true</Hide></Game><Game><Title>Non-game</Title><ID>dlc</ID><Version>[DLC]</Version></Game></LaunchBox>')
   images=library_root/'Images'/'Test'/'Fanart - Background'; images.mkdir(parents=True)
   source=Image.new('RGBA',(1919,1080),'red'); draw=ImageDraw.Draw(source); draw.rectangle((0,360,1918,719),fill='lime'); draw.rectangle((0,720,1918,1079),fill='blue')
   for title in ['Alpha','Beta']: source.save(images/(title+'-01.png'))
   profile=copy.deepcopy(m.load_profiles()['default']); profile['elements']=[e for e in profile['elements'] if e['Tag'] in ['Fan Art','Page curl background']]
   library=m.Library(library_root)
   root=tk.Tk(); root.withdraw(); instance=None
   try:
    with patch.object(ui,'HOME',base),patch.object(ui,'load_profiles',return_value={'default':profile,'platforms':{}}),patch.object(ui,'select_sources',wraps=m.select_sources) as sources,patch.object(library,'games',wraps=library.games) as games:
     instance=ui.App(root); instance.library=library; instance.launchbox.set(str(library_root)); instance.output.set(str(base/'Export'))
     instance.systems.insert('end','Test'); instance.systems.select_set(0); instance.selection_changed()
     def wait(predicate):
      deadline=time.monotonic()+12
      while not predicate() and time.monotonic()<deadline: root.update(); time.sleep(.02)
      root.update(); self.assertTrue(predicate(),instance.status.get())
     def pixel(x,y): return tuple(root.tk.call(str(instance.photo),'get',x,y))
     wait(lambda:instance.photo is not None and not instance.loading and not instance.rendering)
     first_id=instance.context['game'].id
     self.assertIn(first_id,['a','b'])
     self.assertEqual(pixel(200,80),(0,255,0)); self.assertEqual(sources.call_count,1)
     previous=instance.photo; instance.set_crop(0)
     wait(lambda:instance.photo is not previous)
     self.assertEqual(pixel(200,80),(255,0,0)); self.assertEqual(instance.context['game'].id,first_id)
     previous=instance.photo; instance.crop_text.set('100'); instance.crop_from_text()
     wait(lambda:instance.photo is not previous)
     self.assertEqual(pixel(200,80),(0,0,255)); self.assertEqual(sources.call_count,1)
     wait(lambda:instance.settings.is_file() and json.loads(instance.settings.read_text())['background_position']==100)
     previous=instance.photo; original=copy.deepcopy(instance.palettes)
     original_pixel=pixel(950,35); instance.choose_color('main')
     self.assertIsNotNone(instance.picker)
     instance.picker.hex_var.set('#GG0000'); self.assertFalse(instance.picker.valid)
     self.assertEqual(instance.palettes,original)
     instance.picker.hex_var.set('#AABBCC')
     wait(lambda:instance.photo is not previous)
     self.assertEqual(pixel(950,35),(170,187,204)); self.assertEqual(instance.context['game'].id,first_id); self.assertEqual(sources.call_count,1)
     self.assertEqual(json.loads(instance.settings.read_text())['palettes'],original,'Live picker drafts must not be saved')
     previous=instance.photo; instance.picker.cancel()
     wait(lambda:instance.photo is not previous)
     self.assertEqual(instance.palettes,original); self.assertEqual(pixel(950,35),original_pixel)
     previous=instance.photo; instance.choose_color('main'); instance.picker.set_color('#AABBCC'); instance.picker.apply()
     wait(lambda:instance.photo is not previous)
     wait(lambda:json.loads(instance.settings.read_text())['palettes'].get('Test',{}).get('main')=='#AABBCC')
     self.assertIn('#AABBCC',instance.custom_colors)
     # Main-window HEX edits validate before previewing or generating.
     instance.hex_vars['main'].set('#12'); instance.start_generation()
     self.assertFalse(instance.busy); self.assertIn('HEX',instance.status.get())
     self.assertFalse((base/'Export').exists())
     instance.hex_vars['main'].set('aabbcc'); instance.finish_hex('main')
     self.assertEqual(instance.hex_vars['main'].get(),'#AABBCC')
     self.assertEqual(instance.swatches['main'].cget('background'),'#AABBCC')
     # Defaults can be restored without switching games.
     instance.reset_palette(); self.assertNotIn('Test',instance.palettes)
     self.assertEqual(instance.context['game'].id,first_id)
     instance.hex_vars['main'].set('#AABBCC')
     self.assertFalse((base/'Export').exists(),'Live preview must not write output PNGs')
     # A slow older render must not replace a newer crop request.
     gate=threading.Event(); started=threading.Event(); calls=[]
     def slow_render(*args,**kwargs):
      calls.append(1)
      if len(calls)==1: started.set(); gate.wait(6)
      return m.render(*args,**kwargs)
     with patch.object(ui,'render',side_effect=slow_render):
      previous=instance.photo; instance.set_crop(0); wait(started.is_set)
      instance.set_crop(100); root.update(); self.assertIs(instance.photo,previous); gate.set()
      wait(lambda:instance.photo is not previous and not instance.rendering)
      self.assertEqual(pixel(200,80),(0,0,255)); self.assertEqual(len(calls),2)
     self.assertEqual(instance.context['game'].id,first_id); self.assertEqual(sources.call_count,1)
     instance.request_random()
     wait(lambda:instance.context is not None and instance.context['game'].id!=first_id and not instance.loading and not instance.rendering and instance.render_after is None)
     self.assertIn(instance.context['game'].id,['a','b'])
     self.assertEqual(sources.call_count,2); self.assertEqual(games.call_count,1)
     instance.crop_text.set('nan'); instance.crop_from_text(); instance.start_generation()
     self.assertFalse(instance.busy); self.assertIn('Background crop',instance.status.get())
     self.assertLessEqual(root.winfo_reqheight(),890,'The controls and preview should fit the default window')
     # Generate through the actual button handler and verify the copy-ready export.
     instance.title_filter.set(instance.context['game'].title); instance.set_crop(75)
     with patch.object(ui,'generate',wraps=m.generate) as exporter:
      instance.start_generation(); wait(lambda:not instance.busy and (base/'Export'/'copy-map.csv').is_file())
      self.assertEqual(exporter.call_args.kwargs['background_position'],75)
      self.assertEqual(exporter.call_args.kwargs['palette_overrides']['Test']['main'],'#AABBCC')
     output=base/'Export'/'Images'/'Test'/'Arcade - Marquee'/(instance.context['game'].title+'-01.png')
     self.assertTrue(output.is_file())
     with Image.open(output) as generated:
      self.assertGreater(generated.getpixel((400,20))[1],0)
      self.assertEqual(generated.getpixel((400,20))[2],0)
      self.assertGreater(generated.getpixel((400,340))[2],0)
   finally:
    if instance: instance.close()
    else: root.destroy()

if __name__=='__main__': unittest.main()
