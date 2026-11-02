import csv,json,math,tempfile,unittest
from pathlib import Path
from PIL import Image,ImageDraw
import marquee as m
from live_preview import PreviewMediaIndex,random_game

class ControlsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent); self.base=Path(self.tmp.name); self.root=self.base/'LaunchBox'
  (self.root/'Data'/'Platforms').mkdir(parents=True)
  (self.root/'Data'/'Platforms'/'Test.xml').write_text('<LaunchBox><Game><Title>Test Game</Title><ID>game-1</ID><Platform>Test</Platform><Region>us</Region></Game></LaunchBox>')
  self.lib=m.Library(self.root); self.game=self.lib.games('Test')[0]
 def tearDown(self): self.tmp.cleanup()
 def image(self,relative,color='red'):
  path=self.root/relative; path.parent.mkdir(parents=True,exist_ok=True); Image.new('RGBA',(60,30),color).save(path); return path
 def test_crop_percentage_bounds_and_middle(self):
  source=Image.new('RGBA',(100,300),'red'); draw=ImageDraw.Draw(source); draw.rectangle((0,100,99,199),fill='lime'); draw.rectangle((0,200,99,299),fill='blue')
  for position,expected in [(0,(255,0,0,255)),(50,(0,255,0,255)),(100,(0,0,255,255))]:
   result=m.fit_image(source,100,100,'UniformToFill',vertical_position=position)
   self.assertEqual(result.getpixel((50,50)),expected)
  result=m.fit_image(source,100,100,'UniformToFill',vertical_position=25)
  self.assertEqual(result.getpixel((50,0)),(255,0,0,255)); self.assertEqual(result.getpixel((50,99)),(0,255,0,255))
 def test_invalid_crop_is_rejected_before_output(self):
  for value in [-1,101,'',None,'no',float('nan'),float('inf')]:
   with self.assertRaises(ValueError): m.generate(self.lib,self.base/'Output',['Test'],background_position=value)
  self.assertFalse((self.base/'Output').exists())
 def test_copy_ready_default_structure(self):
  rows=m.generate(self.lib,self.base/'Output',['Test'])
  expected=self.base/'Output'/'Images'/'Test'/'Arcade - Marquee'/'Test Game-01.png'
  self.assertEqual(Path(rows[0]['output']),expected); self.assertTrue(expected.is_file())
  with (self.base/'Output'/'copy-map.csv').open(encoding='utf-8-sig') as file: mapping=list(csv.DictReader(file))
  self.assertEqual(Path(mapping[0]['destination_folder']),self.root/'Images'/'Test'/'Arcade - Marquee')
 def test_custom_relative_marquee_path_is_mirrored_even_if_not_created(self):
  self.lib.folders[('test','arcade - marquee')]='Custom artwork/Test/Marquees'
  folder,destination,mode=m.marquee_export_folder(self.lib,'Test',self.base/'Output')
  self.assertEqual(folder,self.base/'Output'/'Custom artwork'/'Test'/'Marquees')
  self.assertEqual(destination,self.root/'Custom artwork'/'Test'/'Marquees')
 def test_external_marquee_folder_is_staged_with_copy_mapping(self):
  destination=self.base/'External art'/'Marquees'
  self.lib.folders[('Test','Arcade - Marquee')]=str(destination)
  folder,target,mode=m.marquee_export_folder(self.lib,'Test',self.base/'Output')
  self.assertEqual(target,destination); self.assertIn('External marquee folders',folder.parts)
  self.assertTrue(folder.is_relative_to(self.base/'Output'))
 def test_relative_escape_does_not_escape_output(self):
  self.lib.folders[('Test','Arcade - Marquee')]='../../outside'
  folder,target,_=m.marquee_export_folder(self.lib,'Test',self.base/'Output')
  self.assertTrue(folder.is_relative_to(self.base/'Output'))
 def test_launchbox_root_cannot_be_a_marquee_destination(self):
  self.lib.folders[('Test','Arcade - Marquee')]='.'
  with self.assertRaises(ValueError): m.marquee_export_folder(self.lib,'Test',self.base/'Output')
 def test_crop_changes_invalidate_generated_image(self):
  background=self.image('Images/Test/Fanart - Background/Test Game-01.png')
  source=Image.new('RGBA',(60,180),'red'); ImageDraw.Draw(source).rectangle((0,60,59,179),fill='blue'); source.save(background)
  output=self.base/'Output'
  first=m.generate(self.lib,output,['Test'],background_position=0)[0]
  self.assertEqual(first['status'],'generated')
  self.assertEqual(m.generate(self.lib,output,['Test'],background_position=0)[0]['status'],'unchanged')
  self.assertEqual(m.generate(self.lib,output,['Test'],background_position=100)[0]['status'],'generated')
 def test_targeted_lookup_matches_batch_region_and_game_id(self):
  self.image('Images/Test/Clear Logo/Japan/Test Game-01.png')
  wanted=self.image('Images/Test/Clear Logo/North America/Test Game.game-1-02.png')
  self.image('Images/Test/Clear Logo/North America/Test Game-01.png')
  batch=m.MediaIndex(self.lib,'Test').choose(self.game,['Clear Logo'])
  live=PreviewMediaIndex(self.lib,'Test',self.game).choose(self.game,['Clear Logo'])
  self.assertEqual(batch,live); self.assertEqual(live[0],wanted)
 def test_targeted_lookup_reports_ambiguous_punctuation(self):
  self.game.title='A B'; self.game.application=''
  self.image('Images/Test/Clear Logo/A-B-01.png'); self.image('Images/Test/Clear Logo/A.B-01.png')
  index=PreviewMediaIndex(self.lib,'Test',self.game)
  self.assertIsNone(index.choose(self.game,['Clear Logo'])[0]); self.assertTrue(index.warnings)
 def test_random_preview_avoids_non_games_and_last_choice(self):
  choices=[m.Game('Alpha','Test','a'),m.Game('Beta','Test','b'),m.Game('ZZZ(notgame): Device','Test','c'),m.Game('[BIOS] Boot','Test','d')]
  for _ in range(8): self.assertEqual(random_game(choices,previous_id='a').id,'b')
  self.assertEqual(random_game(choices,title_filter='Alpha').id,'a')
  with self.assertRaises(ValueError): random_game(choices,title_filter='missing')
  choices.append(m.Game('Alpha','Test','another-edition'))
  for _ in range(8): self.assertEqual(random_game(choices,previous_id='a').id,'b')

 def test_cached_live_render_does_not_reread_source_files(self):
  from unittest.mock import patch
  import copy
  source=Image.new('RGBA',(1919,1080),'red'); ImageDraw.Draw(source).rectangle((0,720,1918,1079),fill='blue')
  path=self.root/'background.png'; source.save(path)
  profile=copy.deepcopy(m.load_profiles()['default']); profile['elements']=[e for e in profile['elements'] if e['Tag']=='Fan Art']
  profile['background_position']=0; cache={}
  first=m.render(None,self.game,profile,{'Fan art':path},image_cache=cache)
  self.assertEqual(first.getpixel((960,180)),(255,0,0))
  profile['background_position']=100
  with patch.object(m,'open_image',side_effect=AssertionError('Unexpected source reread')):
   second=m.render(None,self.game,profile,{'Fan art':path},image_cache=cache)
  self.assertEqual(second.getpixel((960,180)),(0,0,255))

if __name__=='__main__': unittest.main()
