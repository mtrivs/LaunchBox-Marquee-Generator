import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import marquee as m

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent); self.base=Path(self.tmp.name); self.root=self.base/'LaunchBox'
  (self.root/'Data'/'Platforms').mkdir(parents=True)
  (self.root/'Data'/'Platforms'/'Test.xml').write_text('<LaunchBox><Game><Title>Test: Game</Title><ID>game-1</ID><DatabaseID>123</DatabaseID><Platform>Test</Platform><ApplicationPath>Games/Test/game.zip</ApplicationPath><Region>us</Region></Game></LaunchBox>')
  (self.root/'Data'/'Platforms'/'Test.backup.xml').write_text('<LaunchBox/>')
  self.lib=m.Library(self.root); self.game=self.lib.games('Test')[0]
 def tearDown(self): self.tmp.cleanup()
 def image(self,path,color='red'):
  path=self.root/path; path.parent.mkdir(parents=True,exist_ok=True); Image.new('RGBA',(100,60),color).save(path); return path
 def test_discovery(self):
  self.assertEqual(m.Library(self.base).root,self.root); self.assertEqual(list(self.lib.platform_files),['Test']); self.assertEqual(self.game.database_id,'123')
 def test_rom_relocation(self):
  rom=self.root/'Games'/'Test'/'game.zip'; rom.parent.mkdir(parents=True); rom.write_bytes(b'rom')
  self.assertEqual(self.lib.resolve_path(self.game.application),rom)
  self.assertEqual(self.lib.resolve_path('D:/LaunchBox/Games/Test/game.zip'),rom)
 def test_region_and_filename_matching(self):
  wanted=self.image('Images/Test/Clear Logo/North America/Test_ Game-01.png'); self.image('Images/Test/Clear Logo/Japan/Test_ Game-01.png')
  self.assertEqual(m.MediaIndex(self.lib,'Test').choose(self.game,['Clear Logo'])[0],wanted)
 def test_database_id_matching(self):
  wanted=self.image('Images/Test/Clear Logo/123-01.png')
  self.assertEqual(m.MediaIndex(self.lib,'Test').choose(self.game,['Clear Logo'])[0],wanted)
 def test_custom_media_folder(self):
  (self.root/'Data'/'Platforms.xml').write_text('<LaunchBox><PlatformFolder><Platform>Test</Platform><MediaType>Clear Logo</MediaType><FolderPath>Custom/Logos</FolderPath></PlatformFolder></LaunchBox>')
  wanted=self.image('Custom/Logos/Test_ Game-01.png')
  self.assertEqual(m.MediaIndex(m.Library(self.root),'Test').choose(self.game,['Clear Logo'])[0],wanted)
 def test_ambiguous_titles(self):
  self.game.title='A B'; self.game.application=''; self.image('Images/Test/Clear Logo/A-B-01.png'); self.image('Images/Test/Clear Logo/A.B-01.png')
  index=m.MediaIndex(self.lib,'Test'); self.assertIsNone(index.choose(self.game,['Clear Logo'])[0]); self.assertTrue(index.warnings)
 def test_aspect_ratio(self):
  result=m.fit_image(Image.new('RGBA',(100,50),'red'),100,100)
  self.assertEqual(result.getpixel((0,0))[3],0); self.assertEqual(result.getpixel((50,50))[3],255)
 def test_safe_names_and_destination(self):
  self.assertEqual(m.safe_name('Test: Game?'),'Test_ Game_'); self.assertEqual(m.safe_name('CON'),'_CON')
  with self.assertRaises(ValueError): m.ensure_output(self.lib,self.root/'Images'/'Generated')
 def test_incremental_generation(self):
  source=self.image('Images/Test/Clear Logo/Test_ Game-01.png'); original=source.read_bytes()
  profile={'main':'#FAC52B','accent':'#F68936','elements':[{'UIType':'Image','ImageValueType':'Metadata','MetadataImageField':'Game clear logo','W':'848','H':'325','X':'536','Y':'17','Tag':'Logo'}]}
  output=self.base/'Output'
  with patch.object(m,'load_profiles',return_value={'default':profile,'platforms':{'Test':profile}}):
   rows=m.generate(self.lib,output,['Test']); self.assertEqual(rows[0]['status'],'generated'); self.assertEqual(source.read_bytes(),original)
   with Image.open(rows[0]['output']) as result: self.assertEqual(result.size,(1920,360))
   self.assertEqual(m.generate(self.lib,output,['Test'])[0]['status'],'unchanged')
   Image.new('RGBA',(100,60),'green').save(source)
   self.assertEqual(m.generate(self.lib,output,['Test'])[0]['status'],'generated')
  self.assertIn('Game box 3D',rows[0]['missing']); self.assertTrue((output/'media-report.csv').is_file())
 def test_keep_existing_files(self):
  output=self.base/'Output'; folder=output/'Images'/'Test'/'Arcade - Marquee'; folder.mkdir(parents=True); file=folder/'Test_ Game-01.png'; file.write_bytes(b'original')
  row=m.generate(self.lib,output,['Test'])[0]; self.assertEqual(row['status'],'existing-kept'); self.assertEqual(file.read_bytes(),b'original')

 def test_game_specific_media(self):
  self.game.id='specific-id'
  self.image('Images/Test/Clear Logo/Test_ Game-01.png')
  wanted=self.image('Images/Test/Clear Logo/Test_ Game.specific-id-01.png')
  self.assertEqual(m.MediaIndex(self.lib,'Test').choose(self.game,['Clear Logo'])[0],wanted)
 def test_duplicate_title_outputs(self):
  file=self.root/'Data'/'Platforms'/'Test.xml'
  xml=file.read_text(); second=xml.split('<Game>')[1].split('</Game>')[0].replace('game-1','game-2')
  file.write_text(xml.replace('</LaunchBox>','<Game>'+second+'</Game></LaunchBox>'))
  rows=m.generate(m.Library(self.root),self.base/'Output',['Test'])
  self.assertEqual(len(rows),2)
  self.assertNotEqual(rows[0]['output'],rows[1]['output'])
  self.assertTrue(all(r['status']=='generated' for r in rows))

if __name__=='__main__': unittest.main()

