import copy,csv,json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import marquee as m
from io_utils import read_json,atomic_json,sha256,OperationLock
from installation import build_plan,apply_plan,review_restore,restore_backup,copy_verified,rename_verified,inactive_path,NumberedNames
from image_cache import ImageCache
from logo_packs import find_logo

A='11111111-1111-4111-8111-111111111111'; B='22222222-2222-4222-8222-222222222222'
PROFILE={'main':'#AABBCC','elements':[{'Tag':'Page curl background','UIType':'Rectangle','X':'0','Y':'0','W':'1920','H':'360','Zorder':'0'}]}

class BatchInstallTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent); self.base=Path(self.tmp.name); self.root=self.base/'LaunchBox'
  self.data=self.root/'Data'/'Platforms'; self.data.mkdir(parents=True)
  self.write_games(); self.lib=m.Library(self.root); self.output=self.base/'Export'; self.backups=self.base/'Backups'
  self.profile_patch=patch.object(m,'load_profiles',return_value={'default':copy.deepcopy(PROFILE),'platforms':{}}); self.profile_patch.start()
 def tearDown(self): self.profile_patch.stop(); self.tmp.cleanup()
 def write_games(self):
  (self.data/'Test.xml').write_text(f"<LaunchBox><Game><Title>8 Eye's</Title><ID>{A}</ID><Platform>Test</Platform><ApplicationPath>Games/8 Eyes (USA).zip</ApplicationPath></Game><Game><Title>Beta</Title><ID>{B}</ID><Platform>Test</Platform></Game><Game><Title>Hidden</Title><ID>hidden</ID><Hide>true</Hide></Game></LaunchBox>")
 def generated(self): return m.generate(self.lib,self.output,['Test'])
 def old(self,relative,color='red'):
  path=self.root/'Images'/'Test'/'Arcade - Marquee'/relative; path.parent.mkdir(parents=True,exist_ok=True); Image.new('RGB',(40,20),color).save(path); return path
 def selected_plan(self):
  plan=build_plan(self.lib,self.output,['Test'])
  for entry in plan['entries']: entry['ready']=entry.get('ready',False) and entry['game_id']==A
  return plan
 def test_progress_counts_eligible_work_across_systems_and_checkpoints(self):
  (self.data/'Other.xml').write_text('<LaunchBox><Game><Title>Gamma</Title><ID>g</ID><Platform>Other</Platform></Game></LaunchBox>'); lib=m.Library(self.root); events=[]; snapshots=[]
  def checkpoint(path,value):
   if Path(path).name=='marquee-checkpoint.json': snapshots.append(copy.deepcopy(value['progress']))
   atomic_json(path,value)
  with patch.object(m,'atomic_json',side_effect=checkpoint): rows=m.generate(lib,self.output,['Test','Other'],checkpoint_games=1,progress_state=events.append)
  self.assertEqual(len(rows),3); measured=[e for e in events if e['phase']!='preparing']
  self.assertTrue(all(e['overall_total']==3 for e in measured)); self.assertEqual([e['overall_done'] for e in measured],sorted(e['overall_done'] for e in measured))
  self.assertEqual(events[-1]['phase'],'complete'); self.assertEqual(events[-1]['overall_done'],3); self.assertEqual(events[-1]['system_done'],1)
  self.assertTrue({1,2,3}.issubset({e['overall_done'] for e in snapshots}))
  self.assertTrue(all(len(row['sha256'])==64 for row in rows)); self.assertEqual(len(read_json(self.output/'marquee-manifest.json',{})['images']),3)
 def test_stop_and_resume_keep_completed_images(self):
  cancel=threading.Event(); events=[]
  def progress(info):
   events.append(info)
   if info['overall_done']==1: cancel.set()
  rows=m.generate(self.lib,self.output,['Test'],cancelled=cancel,progress_state=progress,checkpoint_games=1)
  self.assertEqual(len(rows),1); self.assertEqual(events[-1]['phase'],'stopped'); self.assertEqual(events[-1]['overall_total'],2)
  completed=Path(rows[0]['output']); before=completed.stat().st_mtime_ns
  rows=self.generated(); self.assertEqual(rows[0]['status'],'unchanged'); self.assertEqual(completed.stat().st_mtime_ns,before); self.assertEqual(rows[1]['status'],'generated')
 def test_crash_between_png_and_checkpoint_recovers_by_pixel_verification(self):
  def fail(path,value):
   if Path(path).name=='marquee-checkpoint.json' and value['progress']['overall_done']==1: raise RuntimeError('Simulated crash')
   atomic_json(path,value)
  with patch.object(m,'atomic_json',side_effect=fail):
   with self.assertRaisesRegex(RuntimeError,'Simulated'): m.generate(self.lib,self.output,['Test'],checkpoint_games=1)
  rows=self.generated(); self.assertEqual(rows[0]['status'],'verified'); self.assertEqual(rows[1]['status'],'generated')
 def test_checkpoint_newer_than_manifest_recovers_without_false_edit_conflict(self):
  self.generated(); previous=read_json(self.output/'marquee-manifest.json',{}); old_signatures={key:item['signature'] for key,item in previous['images'].items()}
  changed=copy.deepcopy(PROFILE); changed['main']='#334455'
  def fail(path,value):
   if Path(path).name=='marquee-manifest.json' and any(item.get('signature')!=old_signatures.get(key) for key,item in value['images'].items()):
    raise RuntimeError('Simulated projection-write crash')
   atomic_json(path,value)
  with patch.object(m,'load_profiles',return_value={'default':changed,'platforms':{}}):
   with patch.object(m,'atomic_json',side_effect=fail):
    with self.assertRaisesRegex(RuntimeError,'projection-write'): m.generate(self.lib,self.output,['Test'],checkpoint_games=1)
   rows=self.generated(); self.assertEqual(rows[0]['status'],'verified'); self.assertEqual(rows[1]['status'],'generated')
 def test_changed_export_is_preserved_and_removed_from_install_manifest(self):
  rows=self.generated(); path=Path(rows[0]['output']); Image.new('RGB',m.SIZE,'green').save(path); before=path.read_bytes()
  rows=self.generated(); self.assertEqual(rows[0]['status'],'changed-kept'); self.assertEqual(path.read_bytes(),before)
  manifest=read_json(self.output/'marquee-manifest.json',{})
  self.assertFalse(any(item['game_id']==A for item in manifest['images'].values()))
 def test_installs_backs_up_matching_variants_and_restores_exact_originals(self):
  self.generated(); canonical=self.old('8 Eye_s.png'); rom=self.old('8 Eyes (USA).png','blue'); regional=self.old('Europe/8 Eye_s-02.jpg','yellow'); unrelated=self.old('Other Game.png','green')
  originals={p:p.read_bytes() for p in [canonical,rom,regional]}; untouched=unrelated.read_bytes()
  plan=self.selected_plan(); selected=[e for e in plan['entries'] if e['ready']]; self.assertEqual(len(selected),1); self.assertEqual(len(selected[0]['older']),1); self.assertEqual(len(selected[0]['retained']),2)
  backup=apply_plan(self.lib,self.output,plan,self.backups)
  self.assertEqual(sha256(selected[0]['destination']),selected[0]['sha256']); self.assertEqual(rom.read_bytes(),originals[rom]); self.assertEqual(regional.read_bytes(),originals[regional]); self.assertEqual(unrelated.read_bytes(),untouched)
  review=review_restore(self.lib,backup); self.assertTrue(review['entries'][0]['ready']); self.assertEqual(restore_backup(self.lib,review),1)
  for path,data in originals.items(): self.assertEqual(path.read_bytes(),data)
  self.assertEqual(unrelated.read_bytes(),untouched); self.assertEqual(read_json(backup/'restore-manifest.json',{})['status'],'restored')
 def test_only_unnumbered_titles_are_renamed_without_unlinking_originals(self):
  self.generated(); paths=[self.old('8 Eye_s.png'),self.old('8 Eye_s-01.png','blue'),self.old('Europe/8 Eyes (USA).png','green'),self.old(f'custom.{A}.jpg','yellow')]
  originals={p:p.read_bytes() for p in paths}; plan=self.selected_plan(); entry=next(e for e in plan['entries'] if e['ready'])
  self.assertEqual(Path(entry['destination']).name,'8 Eye_s.png'); self.assertEqual(len(entry['older']),1); self.assertEqual(len(entry['retained']),3)
  original_unlink=Path.unlink
  def guard(path,*args,**kwargs):
   if path in paths: raise AssertionError('Installation must not delete an original')
   return original_unlink(path,*args,**kwargs)
  with patch.object(Path,'unlink',guard): backup=apply_plan(self.lib,self.output,plan,self.backups)
  for old in entry['older']:
   preserved=Path(old['preserved']); self.assertEqual(preserved.parent,Path(old['path']).parent); self.assertEqual(preserved.suffix,Path(old['path']).suffix); self.assertEqual(preserved.read_bytes(),originals[Path(old['path'])])
  for path in paths[1:]: self.assertEqual(path.read_bytes(),originals[path])
  self.assertTrue(all(not e['ready'] for e in build_plan(self.lib,self.output,['Test'])['entries'] if e['game_id']==A))
  restore_backup(self.lib,review_restore(self.lib,backup))
  for path,data in originals.items(): self.assertEqual(path.read_bytes(),data)
  record=read_json(backup/'restore-manifest.json',{})['entries'][0]
  self.assertEqual(sha256(record['generated_preserved']),entry['sha256'])
 def test_numbering_appends_after_highest_existing_number(self):
  self.generated(); path=self.old('8 Eye_s.png'); one=self.old('8 Eye_s-01.png','blue'); three=self.old('8 Eye_s-03.jpg','green')
  before={p:p.read_bytes() for p in [one,three]}; entry=next(e for e in self.selected_plan()['entries'] if e['game_id']==A)
  self.assertTrue(entry['ready']); self.assertEqual(Path(entry['older'][0]['preserved']).name,'8 Eye_s-04.png')
  for old,data in before.items(): self.assertEqual(old.read_bytes(),data)
 def test_interruption_between_original_renames_can_restore_without_deleting_files(self):
  self.generated(); paths=[self.old('8 Eye_s.png'),self.old('8 Eye_s.jpg','blue')]; originals={p:p.read_bytes() for p in paths}; calls=[]
  def fail(source,destination,digest):
   calls.append(source)
   if len(calls)==2: raise OSError('Power loss between renames')
   rename_verified(source,destination,digest)
  with patch('installation.rename_verified',side_effect=fail):
   with self.assertRaisesRegex(ValueError,'Verified backups'): apply_plan(self.lib,self.output,self.selected_plan(),self.backups)
  backup=next(self.backups.iterdir()); restore_backup(self.lib,review_restore(self.lib,backup))
  for path,data in originals.items(): self.assertEqual(path.read_bytes(),data)
 def test_restore_can_resume_after_generated_image_was_preserved(self):
  self.generated(); old=self.old('8 Eye_s.png'); original=old.read_bytes(); backup=apply_plan(self.lib,self.output,self.selected_plan(),self.backups); calls=[]
  def fail(source,destination,digest):
   calls.append(source)
   if len(calls)==2: raise OSError('Power loss during restore')
   rename_verified(source,destination,digest)
  with patch('installation.rename_verified',side_effect=fail):
   with self.assertRaisesRegex(OSError,'Power loss'): restore_backup(self.lib,review_restore(self.lib,backup))
  self.assertEqual(restore_backup(self.lib,review_restore(self.lib,backup)),1); self.assertEqual(old.read_bytes(),original)
 def test_repeated_install_restore_cycles_keep_each_generated_copy(self):
  self.generated(); path=self.old('8 Eye_s.png'); original=path.read_bytes(); generated=[]
  for _ in range(2):
   backup=apply_plan(self.lib,self.output,self.selected_plan(),self.backups); restore_backup(self.lib,review_restore(self.lib,backup)); generated.append(read_json(backup/'restore-manifest.json',{})['entries'][0]['generated_preserved'])
   self.assertEqual(path.read_bytes(),original)
  self.assertNotEqual(generated[0],generated[1]); self.assertTrue(all(Path(p).is_file() for p in generated))
 def test_version_one_backup_remains_restorable(self):
  self.generated(); path=self.old('8 Eye_s.png'); original=path.read_bytes(); backup=apply_plan(self.lib,self.output,self.selected_plan(),self.backups)
  journal=read_json(backup/'restore-manifest.json',{}); journal['version']=1
  for old in journal['entries'][0]['older']: Path(old['preserved']).unlink()
  atomic_json(backup/'restore-manifest.json',journal); self.assertEqual(restore_backup(self.lib,review_restore(self.lib,backup)),1); self.assertEqual(path.read_bytes(),original)
 def test_super_mario_example_uses_03_and_keeps_01_and_02(self):
  text=(self.data/'Test.xml').read_text().replace("8 Eye's",'Super Mario Bros'); (self.data/'Test.xml').write_text(text); self.generated()
  original=self.old('Super Mario Bros.png'); one=self.old('Super Mario Bros-01.png','blue'); two=self.old('Super Mario Bros-02.png','green'); before={p:p.read_bytes() for p in [original,one,two]}
  plan=self.selected_plan(); entry=next(e for e in plan['entries'] if e['ready']); renamed=original.with_name('Super Mario Bros-03.png')
  self.assertEqual(Path(entry['older'][0]['preserved']),renamed)
  backup=apply_plan(self.lib,self.output,plan,self.backups)
  self.assertEqual(renamed.read_bytes(),before[original]); self.assertEqual(sha256(original),entry['sha256'])
  for path in [one,two]: self.assertEqual(path.read_bytes(),before[path])
  self.assertFalse(any(p.suffix=='.disabled' for p in original.parent.iterdir()))
  restore_backup(self.lib,review_restore(self.lib,backup))
  for path,data in before.items(): self.assertEqual(path.read_bytes(),data)
  self.assertEqual(sha256(original.with_name('Super Mario Bros-04.png')),entry['sha256'])
 def test_new_numbered_file_after_install_review_requires_new_review(self):
  self.generated(); original=self.old('8 Eye_s.png'); before=original.read_bytes(); plan=self.selected_plan(); occupied=self.old('8 Eye_s-01.png','blue'); occupied_bytes=occupied.read_bytes()
  with self.assertRaisesRegex(ValueError,'changed after review'): apply_plan(self.lib,self.output,plan,self.backups)
  self.assertEqual(original.read_bytes(),before); self.assertEqual(occupied.read_bytes(),occupied_bytes)
 def test_new_numbered_file_after_restore_review_requires_new_review(self):
  self.generated(); original=self.old('8 Eye_s.png'); backup=apply_plan(self.lib,self.output,self.selected_plan(),self.backups); review=review_restore(self.lib,backup)
  chosen=Path(review['entries'][0]['generated_preserved']); Image.new('RGB',(20,20),'green').save(chosen); before=original.read_bytes(); occupied=chosen.read_bytes()
  with self.assertRaisesRegex(ValueError,'Numbered filenames changed'): restore_backup(self.lib,review)
  self.assertEqual(original.read_bytes(),before); self.assertEqual(chosen.read_bytes(),occupied)
 def test_numbering_is_case_insensitive_across_formats_and_grows_past_99(self):
  folder=self.base/'Numbers'; folder.mkdir(); (folder/'EXAMPLE-099.jpg').write_bytes(b'keep')
  allocator=NumberedNames(); self.assertEqual(allocator.next(folder/'Example.png').name,'Example-100.png'); self.assertEqual(allocator.next(folder/'Example.JPG').name,'Example-101.JPG')
 def test_version_two_backup_remains_restorable(self):
  self.generated(); path=self.old('8 Eye_s.png'); original=path.read_bytes(); backup=apply_plan(self.lib,self.output,self.selected_plan(),self.backups)
  journal=read_json(backup/'restore-manifest.json',{}); journal['version']=2; item=journal['entries'][0]
  for old in item['older']:
   legacy=inactive_path(old['path'],old['sha256']); Path(old['preserved']).rename(legacy); old['preserved']=str(legacy)
  item['generated_preserved']=str(inactive_path(item['destination'],item['sha256']+journal['operation_id'],'generated'))
  atomic_json(backup/'restore-manifest.json',journal); self.assertEqual(restore_backup(self.lib,review_restore(self.lib,backup)),1); self.assertEqual(path.read_bytes(),original)
 def test_backup_inside_launchbox_is_rejected_before_changes(self):
  self.generated(); old=self.old('8 Eye_s-01.png'); before=old.read_bytes()
  with self.assertRaisesRegex(ValueError,'outside'): apply_plan(self.lib,self.output,self.selected_plan(),self.root/'Backups')
  self.assertEqual(old.read_bytes(),before)
 def test_changed_files_invalidate_install_review(self):
  self.generated(); old=self.old('8 Eye_s-01.png'); plan=self.selected_plan(); Image.new('RGB',(40,20),'green').save(old); before=old.read_bytes()
  with self.assertRaisesRegex(ValueError,'changed after review'): apply_plan(self.lib,self.output,plan,self.backups)
  self.assertEqual(old.read_bytes(),before); self.assertFalse(self.backups.exists())
 def test_added_matching_variant_invalidates_review(self):
  self.generated(); self.old('8 Eye_s-01.png'); plan=self.selected_plan(); self.old('8 Eyes (USA).png')
  with self.assertRaisesRegex(ValueError,'changed after review'): apply_plan(self.lib,self.output,plan,self.backups)
 def test_interrupted_install_has_a_working_restore_manifest(self):
  self.generated(); old=self.old('8 Eye_s.png'); before=old.read_bytes(); plan=self.selected_plan()
  def fail(source,destination,digest):
   if Path(destination)==old.with_name('8 Eye_s.png'): raise OSError('Simulated install failure')
   copy_verified(source,destination,digest)
  with patch('installation.copy_verified',side_effect=fail):
   with self.assertRaisesRegex(ValueError,'Verified backups'): apply_plan(self.lib,self.output,plan,self.backups)
  backup=next(self.backups.iterdir()); self.assertFalse(old.exists()); restore_backup(self.lib,review_restore(self.lib,backup)); self.assertEqual(old.read_bytes(),before)
 def test_restore_does_not_overwrite_newer_artwork(self):
  self.generated(); target=self.old('8 Eye_s.png'); backup=apply_plan(self.lib,self.output,self.selected_plan(),self.backups)
  Image.new('RGB',m.SIZE,'green').save(target); before=target.read_bytes(); review=review_restore(self.lib,backup)
  self.assertFalse(review['entries'][0]['ready'])
  with self.assertRaisesRegex(ValueError,'no safe'): restore_backup(self.lib,review)
  self.assertEqual(target.read_bytes(),before)
 def test_shared_older_images_are_blocked(self):
  text=(self.data/'Test.xml').read_text().replace('<Title>Beta</Title>',"<Title>8 Eye's</Title>")
  (self.data/'Test.xml').write_text(text); self.generated(); old=self.old('8 Eye_s-01.png'); before=old.read_bytes()
  plan=build_plan(self.lib,self.output,['Test']); self.assertTrue(all(not e['ready'] for e in plan['entries'])); self.assertTrue(all('ambiguous' in e['reason'] for e in plan['entries'])); self.assertEqual(old.read_bytes(),before)
 def test_exact_and_stripped_matches_to_different_games_are_shared(self):
  text=(self.data/'Test.xml').read_text().replace("8 Eye's",'Example').replace('<Title>Beta</Title>','<Title>The Example</Title>')
  (self.data/'Test.xml').write_text(text); self.generated(); old=self.old('Example.png'); before=old.read_bytes()
  plan=build_plan(self.lib,self.output,['Test'])
  self.assertTrue(all(not e['ready'] for e in plan['entries'])); self.assertEqual(old.read_bytes(),before)
 def test_library_lock_also_blocks_a_different_export_folder(self):
  from io_utils import LibraryLock
  self.generated(); old=self.old('8 Eye_s-01.png'); plan=self.selected_plan(); before=old.read_bytes()
  with LibraryLock(self.lib.root):
   with self.assertRaisesRegex(ValueError,'Another operation'): apply_plan(self.lib,self.output,plan,self.backups)
  self.assertEqual(old.read_bytes(),before)
 def test_zero_eligible_system_finishes_without_invalid_percentages(self):
  (self.data/'Empty.xml').write_text('<LaunchBox><Game><Title>Hidden</Title><Hide>true</Hide></Game></LaunchBox>'); lib=m.Library(self.root); events=[]
  rows=m.generate(lib,self.output,['Empty'],progress_state=events.append)
  self.assertEqual(rows,[]); self.assertEqual(events[-1]['phase'],'complete'); self.assertEqual(events[-1]['overall_total'],0); self.assertEqual(events[-1]['system_total'],0)
 def test_verified_guid_exports_remain_installable_after_moving_folder(self):
  rows=self.generated(); source=Path(rows[0]['output']); old_key=source.relative_to(self.output).as_posix()
  moved=source.with_name(f'Short.{A}-01.png'); source.rename(moved)
  manifest=read_json(self.output/'marquee-manifest.json',{}); record=manifest['images'].pop(old_key); manifest['images'][moved.relative_to(self.output).as_posix()]=record
  atomic_json(self.output/'marquee-manifest.json',manifest); plan=self.selected_plan(); entry=next(e for e in plan['entries'] if e['game_id']==A)
  self.assertTrue(entry['ready'],entry['reason']); self.assertEqual(Path(entry['destination']).name,'8 Eye_s.png')
 def test_manifest_escape_is_blocked(self):
  self.generated(); manifest=read_json(self.output/'marquee-manifest.json',{}); item=next(iter(manifest['images'].values())); manifest['images']={'../outside.png':item}; atomic_json(self.output/'marquee-manifest.json',manifest)
  plan=build_plan(self.lib,self.output,['Test']); self.assertFalse(plan['entries'][0]['ready']); self.assertIn('escapes',plan['entries'][0]['reason'])
 def test_atomic_copy_refuses_unexpected_existing_destination(self):
  source=self.base/'source.bin'; source.write_bytes(b'expected'); target=self.base/'target.bin'; target.write_bytes(b'newer')
  with self.assertRaises(OSError): copy_verified(source,target,sha256(source))
  self.assertEqual(target.read_bytes(),b'newer')
 def test_operation_lock_prevents_overlapping_writers(self):
  self.output.mkdir()
  with OperationLock(self.output):
   with self.assertRaisesRegex(ValueError,'Another operation'):
    with OperationLock(self.output): pass
  with OperationLock(self.output): pass

class CacheAndPackTests(unittest.TestCase):
 def test_source_cache_uses_byte_budget_and_rejects_oversize_images(self):
  cache=ImageCache(800); image=Image.new('RGBA',(10,10)); cache['a']=image; cache['b']=image; cache['a']; cache['c']=image
  self.assertNotIn('b',cache); self.assertIn('a',cache); cache['big']=Image.new('RGBA',(100,100)); self.assertNotIn('big',cache)
  cache.set_budget(400); self.assertLessEqual(cache.bytes,400)
 def test_oversize_source_still_renders_without_retaining_it(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
   path=Path(temporary)/'image.png'; Image.new('RGB',(100,100),'red').save(path)
   profile=copy.deepcopy(PROFILE); profile['elements']=[{'UIType':'Image','Tag':'Fan Art','ImageValueType':'Metadata','MetadataImageField':'Fan art','W':'1920','H':'360','Stretch':'Fill'}]
   cache=ImageCache(1); rendered=m.render(None,m.Game('Test','Test'),profile,{'Fan art':path},image_cache=cache)
   self.assertEqual(rendered.getpixel((0,0)),(255,0,0)); self.assertEqual(cache.bytes,0)
 def test_logo_pack_auto_legacy_explicit_and_scrape_as_fallback(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
   root=Path(temporary); (root/'Data'/'Platforms').mkdir(parents=True); lib=m.Library(root); lib.scrape_as['Test']='Nintendo Switch'
   def image(relative,color):
    p=root/relative; p.parent.mkdir(parents=True,exist_ok=True); Image.new('RGBA',(40,20),color).save(p); return p
   legacy=image('Images/Platforms/Test/Clear Logo/logo.png','red')
   nostalgic=image('Images/Media Packs/Platform Clear Logos/Nostalgic Platform Clear Logos/Nintendo Switch.png','blue')
   chosen=image('Images/Media Packs/Platform Clear Logos/Custom/Platforms/Test.png','green')
   self.assertEqual(find_logo(lib,'Test'),legacy); self.assertEqual(find_logo(lib,'Test','Custom'),chosen)
   legacy.unlink(); self.assertEqual(find_logo(lib,'Test'),nostalgic); self.assertEqual(find_logo(lib,'Test','missing'),nostalgic)

if __name__=='__main__': unittest.main()
