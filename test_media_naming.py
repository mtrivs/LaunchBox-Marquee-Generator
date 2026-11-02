"""LaunchBox media naming regression checks, including the 8 Eye's mismatch."""
import csv,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree.ElementTree import Element,SubElement,tostring
from PIL import Image
import marquee as m
from live_preview import PreviewMediaIndex

class MediaNamingTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
  self.base=Path(self.tmp.name); self.root=self.base/'LaunchBox'
  self.data=self.root/'Data'/'Platforms'/'Test.xml'; self.data.parent.mkdir(parents=True)
 def tearDown(self): self.tmp.cleanup()
 def records(self,entries):
  root=Element('LaunchBox')
  for i,title in enumerate(entries):
   game=SubElement(root,'Game')
   for name,value in [('Title',title),('ID',f'00000000-0000-0000-0000-{i:012d}'),('Platform','Test')]: SubElement(game,name).text=value
  self.data.write_bytes(tostring(root)); return m.Library(self.root)
 def image(self,name,color='red'):
  p=self.root/'Images'/'Test'/'Clear Logo'/name; p.parent.mkdir(parents=True,exist_ok=True)
  Image.new('RGBA',(40,20),color).save(p); return p
 def test_apostrophe_and_runs_follow_launchbox_convention(self):
  self.assertEqual(m.media_name("8 Eye's"),'8 Eye_s')
  self.assertEqual(m.media_name('Game/:?'),'Game_')
  self.assertEqual(m.media_name("A'\"/:B"),'A_B')
  self.assertEqual(m.media_name("Game's: Title?"),'Game_s_ Title_')
  self.assertEqual(m.media_name('Alpha__Beta'),'Alpha__Beta')
  self.assertEqual(m.safe_name("Console's Name"),"Console's Name",'Filesystem folder names must keep legal apostrophes')
 def test_8_eyes_export_exact_filename_and_resolution(self):
  lib=self.records(["8 Eye's"]); rows=m.generate(lib,self.base/'Output',['Test'])
  p=Path(rows[0]['output']); self.assertEqual(p.name,'8 Eye_s-01.png')
  self.assertEqual(p.parent,self.base/'Output'/'Images'/'Test'/'Arcade - Marquee')
  self.assertEqual(rows[0]['status'],'generated')
  with Image.open(p) as source: self.assertEqual(source.size,(1920,360))
  self.assertEqual(m.generate(lib,self.base/'Output',['Test'])[0]['status'],'unchanged')
 def test_canonical_media_wins_in_batch_and_live_lookup(self):
  lib=self.records(["8 Eye's"]); game=lib.games('Test')[0]
  canonical=self.image('8 Eye_s-01.png'); legacy=self.image("8 Eye's-01.png",'blue')
  for cls in [lambda:m.MediaIndex(lib,'Test'),lambda:PreviewMediaIndex(lib,'Test',game)]:
   self.assertEqual(cls().choose(game,['Clear Logo'])[0],canonical)
  canonical.unlink()
  for cls in [lambda:m.MediaIndex(lib,'Test'),lambda:PreviewMediaIndex(lib,'Test',game)]:
   self.assertEqual(cls().choose(game,['Clear Logo'])[0],legacy)
 def test_consecutive_characters_canonical_specific_media(self):
  lib=self.records(['Game/:?']); game=lib.games('Test')[0]
  self.image('Game_-01.png'); wanted=self.image(f'Game_.{game.id}-01.png')
  for index in [m.MediaIndex(lib,'Test'),PreviewMediaIndex(lib,'Test',game)]:
   self.assertEqual(index.choose(game,['Clear Logo'])[0],wanted)
 def test_legacy_repeated_underscore_source_still_works(self):
  lib=self.records(['Game/:?']); game=lib.games('Test')[0]; wanted=self.image('Game___-01.png')
  for index in [m.MediaIndex(lib,'Test'),PreviewMediaIndex(lib,'Test',game)]:
   self.assertEqual(index.choose(game,['Clear Logo'])[0],wanted)
 def test_collisions_after_launchbox_normalization_keep_game_ids(self):
  lib=self.records(["Game's",'Game_s','Game/:?','Game_'])
  rows=m.generate(lib,self.base/'Output',['Test'])
  self.assertEqual(len({r['output'] for r in rows}),4)
  for row in rows:
   self.assertEqual(row['status'],'generated'); self.assertIn(row['game_id'],Path(row['output']).stem)
 def test_shortened_and_reserved_titles_keep_guid_matching(self):
  lib=self.records(['L'*170,'CON'])
  rows=m.generate(lib,self.base/'Output',['Test'])
  for row in rows:
   self.assertEqual(row['status'],'generated'); self.assertIn(row['game_id'],Path(row['output']).stem)

if __name__=='__main__': unittest.main()
