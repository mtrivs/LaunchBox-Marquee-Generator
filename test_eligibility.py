"""Visibility filtering must agree between batch generation and live previews."""
import csv,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import marquee as m
from live_preview import random_game

class EligibilityTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
  self.base=Path(self.tmp.name); self.root=self.base/'LaunchBox'
  self.data=self.root/'Data'/'Platforms'/'Test.xml'; self.data.parent.mkdir(parents=True)
 def tearDown(self): self.tmp.cleanup()
 def records(self,records):
  from xml.etree.ElementTree import Element,SubElement,tostring
  root=Element('LaunchBox')
  for fields in records:
   node=SubElement(root,'Game')
   for name,value in fields.items(): SubElement(node,name).text=value
  self.data.write_bytes(tostring(root)); return m.Library(self.root)
 def report(self,name):
  with (self.base/'Output'/name).open(encoding='utf-8-sig') as file: return list(csv.DictReader(file))
 def test_reads_launchbox_hide_and_version(self):
  lib=self.records([{'Title':str(i),'Hide':value,'Version':'[DLC]'} for i,value in enumerate(['true',' True ','TRUE','1','false','0',''])]+[{'Title':'missing'}])
  games=lib.games('Test')
  self.assertEqual([g.hidden for g in games],[True,True,True,True,False,False,False,False])
  self.assertEqual(games[0].version,'[DLC]')
 def test_explicit_markers_not_loose_words(self):
  for field in ['title','application','version']:
   for marker in ['[DLC]','[Update]','[BIOS]','[Device]','(bios)','[ dlc ]']:
    kwargs={field:'Games/'+marker+'.nsp' if field=='application' else marker}
    if field!='title': kwargs['title']='Test'
    self.assertTrue(m.exclusion_reason(m.Game(platform='Test',**kwargs)),(field,marker))
  for title in ['DLC Quest','BioShock','System BIOS Adventure','Game: Updated Edition','Device 6','Update Quest']:
   self.assertFalse(m.exclusion_reason(m.Game(title,'Test',application='Games/[DLC]/base.nsp')),title)
  self.assertTrue(m.exclusion_reason(m.Game('ZZZ(notgame): Device','Test')))
 def test_hidden_reason_takes_priority(self):
  self.assertEqual(m.exclusion_reason(m.Game('Hidden [DLC]','Test',hidden=True)),'Hidden in LaunchBox')
 def test_batch_filters_before_limit_and_never_looks_up_excluded_games(self):
  lib=self.records([{'Title':'A hidden','ID':'h','Hide':'true'}, {'Title':'B [BIOS]','ID':'b'}, {'Title':'C DLC','ID':'d','ApplicationPath':'C [DLC].nsp'}, {'Title':'D Update','ID':'u','Version':'[Update]'}, {'Title':'E Visible','ID':'v'}, {'Title':'F Visible','ID':'f'}])
  before=self.data.read_bytes()
  with patch.object(m,'select_sources',wraps=m.select_sources) as lookup:
   rows=m.generate(lib,self.base/'Output',['Test'],limit=1)
  self.assertEqual([r['title'] for r in rows],['E Visible'])
  self.assertEqual(lookup.call_count,1)
  self.assertEqual(len(self.report('excluded-games.csv')),4)
  self.assertEqual(len(list((self.base/'Output').rglob('*.png'))),1)
  self.assertEqual(self.data.read_bytes(),before)
 def test_existing_excluded_artwork_is_preserved_and_report_clears(self):
  lib=self.records([{'Title':'Hidden','Hide':'true'}])
  folder=self.base/'Output'/'Images'/'Test'/'Arcade - Marquee'; folder.mkdir(parents=True)
  old=folder/'Hidden-01.png'; old.write_bytes(b'existing artwork')
  self.assertEqual(m.generate(lib,self.base/'Output',['Test'],force=True),[])
  self.assertEqual(old.read_bytes(),b'existing artwork')
  self.assertEqual(len(self.report('excluded-games.csv')),1); self.assertEqual(self.report('media-report.csv'),[])
  self.records([{'Title':'Visible'}]); m.generate(lib,self.base/'Output',['Test'])
  self.assertEqual(self.report('excluded-games.csv'),[])
 def test_random_preview_and_batch_use_same_eligibility(self):
  lib=self.records([{'Title':'Hidden','Hide':'true','ID':'h'}, {'Title':'DLC','Version':'[DLC]','ID':'d'}, {'Title':'[Device] Test','ID':'n'}, {'Title':'Visible','ID':'v'}])
  games=lib.games('Test')
  for _ in range(12): self.assertEqual(random_game(games).id,'v')
  with self.assertRaisesRegex(ValueError,'No visible games'): random_game(games,title_filter='Hidden')
  rows=m.generate(lib,self.base/'Output',['Test'])
  self.assertEqual([r['game_id'] for r in rows],['v'])
 def test_title_filter_applies_to_exclusion_report(self):
  lib=self.records([{'Title':'One hidden','Hide':'true'},{'Title':'Two hidden','Hide':'true'},{'Title':'One visible'}])
  rows=m.generate(lib,self.base/'Output',['Test'],title='One')
  self.assertEqual([r['title'] for r in rows],['One visible'])
  self.assertEqual([r['title'] for r in self.report('excluded-games.csv')],['One hidden'])

if __name__=='__main__': unittest.main()
