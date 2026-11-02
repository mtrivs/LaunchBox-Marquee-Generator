"""UI integration coverage for measured progress and reviewed workflows."""
import copy,json,tempfile,time,threading,tkinter as tk,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import marquee as m
import app as ui
from workflow_ui import ReviewWindow
from installation import build_plan,review_restore
from logo_packs import packs
from test_batch_install import PROFILE,A

class WorkflowAppTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent); self.base=Path(self.tmp.name); self.root_path=self.base/'LaunchBox'
  data=self.root_path/'Data'/'Platforms'; data.mkdir(parents=True)
  (data/'Test.xml').write_text(f'<LaunchBox><Game><Title>Example</Title><ID>{A}</ID><Platform>Test</Platform></Game></LaunchBox>')
  self.library=m.Library(self.root_path); self.root=tk.Tk(); self.root.withdraw(); self.dialog=None
  self.home=patch.object(ui,'HOME',self.base); self.home.start()
  self.profiles=patch.object(ui,'load_profiles',return_value={'default':copy.deepcopy(PROFILE),'platforms':{}}); self.profiles.start()
  self.generator_profiles=patch.object(m,'load_profiles',return_value={'default':copy.deepcopy(PROFILE),'platforms':{}}); self.generator_profiles.start()
  self.errors=[]; self.messages=patch('workflow_ui.messagebox.showerror',side_effect=lambda *args,**kwargs:self.errors.append(args)); self.messages.start()
  self.app=ui.App(self.root); self.app.library=self.library; self.app.launchbox.set(str(self.root_path)); self.app.output.set(str(self.base/'Export')); self.app.systems.insert('end','Test'); self.app.systems.select_set(0); self.app.show_palette()
 def tearDown(self):
  if self.dialog:
   try:
    if self.dialog.window.winfo_exists(): self.dialog.close()
   except tk.TclError: pass
  self.root.update_idletasks(); self.app.busy=False; self.app.close(); self.messages.stop(); self.generator_profiles.stop(); self.profiles.stop(); self.home.stop(); self.tmp.cleanup()
 def wait(self,predicate):
  deadline=time.monotonic()+15
  while not predicate() and time.monotonic()<deadline: self.root.update(); time.sleep(.02)
  self.root.update(); self.assertTrue(predicate(),self.app.status.get()); self.assertFalse(self.errors,self.errors)
 def test_preview_animation_and_counted_batch_bars(self):
  self.app.loading=True; self.app.last_progress=None; self.app.update_activity(); self.assertEqual(str(self.app.progress.cget('mode')),'indeterminate')
  self.app.loading=False; self.app.job_kind='batch'; self.app.set_busy(True)
  info={'phase':'batch','overall_done':3,'overall_total':8,'system_done':1,'system_total':4,'system_index':2,'system_count':3,'platform':'Test'}
  self.app.show_batch_progress(info); self.assertEqual(str(self.app.progress.cget('mode')),'determinate'); self.assertEqual(float(self.app.progress.cget('value')),37.5); self.assertEqual(float(self.app.system_progress.cget('value')),25)
  self.assertIn('3 / 8',self.app.progress_text.get()); self.assertIn('Test',self.app.system_progress_text.get())
  self.app.update_activity(); self.assertEqual(float(self.app.progress.cget('value')),37.5)
  info.update(phase='stopped'); self.app.show_batch_progress(info); self.app.set_busy(False)
  self.assertEqual(float(self.app.progress.cget('value')),37.5,'Stopping must not show 100 percent')
  self.app.loading=True; self.app.last_progress=None; self.app.update_activity(); self.assertEqual(str(self.app.progress.cget('mode')),'indeterminate'); self.app.loading=False
 def test_review_requires_backup_choice_and_actual_install_restore_buttons(self):
  output=Path(self.app.output.get()); m.generate(self.library,output,['Test'])
  old=self.root_path/'Images'/'Test'/'Arcade - Marquee'/'Example.png'; old.parent.mkdir(parents=True,exist_ok=True); Image.new('RGB',(30,20),'red').save(old); original=old.read_bytes()
  self.dialog=ReviewWindow(self.app,build_plan(self.library,output,['Test']),'install')
  self.assertEqual(str(self.dialog.action.cget('state')),'disabled')
  backups=self.base/'Backups'
  with patch('workflow_ui.filedialog.askdirectory',return_value=str(backups)): self.dialog.choose_backup()
  self.assertEqual(str(self.dialog.action.cget('state')),'normal'); self.assertEqual(old.read_bytes(),original,'Review alone must not alter images')
  self.dialog.action.invoke(); self.dialog=None; self.wait(lambda:not self.app.busy and backups.is_dir())
  self.assertNotEqual(old.read_bytes(),original); self.assertEqual(old.with_name('Example-01.png').read_bytes(),original); backup=next(backups.iterdir())
  self.dialog=ReviewWindow(self.app,review_restore(self.library,backup),'restore')
  self.assertEqual(str(self.dialog.action.cget('state')),'normal'); self.dialog.action.invoke(); self.dialog=None
  self.wait(lambda:not self.app.busy and old.read_bytes()==original)
  self.assertIn('Restored',self.app.status.get())
 def test_logo_choice_updates_same_game_without_mutating_inflight_context(self):
  logo=self.root_path/'Images'/'Media Packs'/'Platform Clear Logos'/'Custom'/'Test.png'; logo.parent.mkdir(parents=True); Image.new('RGBA',(40,20),'red').save(logo)
  self.app.available_packs=packs(self.library); self.app.selection_changed()
  self.wait(lambda:self.app.context is not None and not self.app.loading and not self.app.rendering and self.app.render_after is None)
  original=self.app.context; old_sources=dict(original['selected']); original_id=original['game'].id
  self.app.logo_var.set('Custom'); self.app.logo_changed()
  self.wait(lambda:self.app.context is not original and not self.app.rendering and self.app.render_after is None)
  self.assertEqual(self.app.context['game'].id,original_id); self.assertEqual(original['selected'],old_sources)
  self.assertEqual(self.app.context['selected']['Platform clear logo'],logo)
 def test_actual_generation_handler_uses_measured_progress_and_checkpoint_option(self):
  self.app.selection_changed()
  self.wait(lambda:self.app.photo is not None and not self.app.loading and not self.app.rendering and self.app.render_after is None)
  self.app.options['checkpoint_games']=1; self.app.start_generation()
  self.wait(lambda:not self.app.busy and (Path(self.app.output.get())/'marquee-checkpoint.json').is_file())
  self.assertEqual(str(self.app.progress.cget('mode')),'determinate'); self.assertEqual(float(self.app.progress.cget('value')),100)
  self.assertEqual(float(self.app.system_progress.cget('value')),100); self.assertIn('1 / 1',self.app.progress_text.get())
  self.assertLessEqual(self.root.winfo_reqheight(),890)

 def test_preview_focus_and_checkbox_generation_are_independent(self):
  (self.root_path/'Data'/'Platforms'/'Other.xml').write_text('<LaunchBox><Game><Title>Other game</Title><ID>other</ID><Platform>Other</Platform></Game></LaunchBox>')
  self.app.library=m.Library(self.root_path); self.app.systems.insert('end','Other')
  self.app.systems.rows[1]['name_button'].invoke()
  self.wait(lambda:self.app.context is not None and self.app.context['platform']=='Other' and not self.app.loading and not self.app.rendering and self.app.render_after is None)
  self.assertEqual(self.app.selected(),['Test']); self.assertEqual(self.app.preview_selection(),['Other'])
  context=self.app.context; self.app.hex_vars['main'].set('#12')
  self.assertIn('main',self.app.invalid_colors)
  self.app.systems.rows[1]['checkbutton'].invoke(); self.assertEqual(self.app.selected(),['Test','Other']); self.assertIn('main',self.app.invalid_colors)
  self.assertIs(self.app.context,context,'Checking systems must not reload the current preview')
  self.app.systems.rows[1]['checkbutton'].invoke(); self.app.hex_vars['main'].set('#123456')
  self.wait(lambda:not self.app.rendering and self.app.render_after is None)
  with patch.object(ui,'generate',wraps=m.generate) as generator:
   self.app.generate_button.invoke(); self.wait(lambda:not self.app.busy and (self.base/'Export'/'marquee-manifest.json').is_file())
   self.assertEqual(generator.call_args.args[2],['Test'])
  self.assertFalse((self.base/'Export'/'Images'/'Other').exists())
 def test_checked_count_clear_and_disabled_generate_keep_preview_focus(self):
  self.app.selection_changed(); self.wait(lambda:self.app.context is not None and not self.app.loading and not self.app.rendering and self.app.render_after is None)
  context=self.app.context; self.assertEqual(self.app.checked_count.get(),'1 system checked')
  self.app.clear_button.invoke(); self.assertEqual(self.app.checked_count.get(),'0 systems checked')
  self.assertEqual(str(self.app.generate_button.cget('state')),'disabled'); self.assertIs(self.app.context,context)
  self.assertEqual(str(self.app.random_button.cget('state')),'normal')
  self.app.select_all_button.invoke(); self.assertEqual(self.app.checked_count.get(),'1 system checked')
  self.assertEqual(str(self.app.generate_button.cget('state')),'normal')
 def test_status_names_current_game_before_render_and_stop_preserves_dual_counts(self):
  data=self.root_path/'Data'/'Platforms'/'Test.xml'
  data.write_text(data.read_text().replace('</LaunchBox>','<Game><Title>Second</Title><ID>second</ID><Platform>Test</Platform></Game></LaunchBox>'))
  started=threading.Event(); release=threading.Event(); original=m.render
  def slow_render(*args,**kwargs):
   started.set(); release.wait(8); return original(*args,**kwargs)
  with patch.object(m,'render',side_effect=slow_render):
   try:
    self.app.start_generation(); self.wait(lambda:started.is_set() and self.app.status.get()=='Now generating: Test - Example')
    self.assertEqual(float(self.app.progress.cget('value')),0); self.assertEqual(float(self.app.system_progress.cget('value')),0)
    self.assertTrue(self.app.system_progress_frame.winfo_manager()); self.assertEqual(self.app.stop_button.grid_info()['column'],1)
    self.assertFalse(self.app.systems.enabled); self.assertEqual(str(self.app.rebuild_check.cget('state')),'disabled')
    self.app.stop_button.invoke(); self.assertTrue(self.app.cancelled.is_set()); self.assertTrue(self.app.busy)
    self.assertEqual(str(self.app.stop_button.cget('state')),'disabled')
   finally: release.set()
   self.wait(lambda:not self.app.busy)
  self.assertIn('Stopped',self.app.status.get()); self.assertEqual(float(self.app.progress.cget('value')),50)
  self.assertEqual(float(self.app.system_progress.cget('value')),50); self.assertTrue(self.app.system_progress_frame.winfo_manager())
  self.assertIn('1 / 2',self.app.progress_text.get()); self.assertIn('1 / 2',self.app.system_progress_text.get())
 def test_refresh_icon_rerolls_preview_without_checked_systems_or_output_files(self):
  data=self.root_path/'Data'/'Platforms'/'Test.xml'
  data.write_text(data.read_text().replace('</LaunchBox>','<Game><Title>Second</Title><ID>second</ID><Platform>Test</Platform></Game></LaunchBox>'))
  self.app.selection_changed(); self.wait(lambda:self.app.photo is not None and not self.app.loading and not self.app.rendering and self.app.render_after is None)
  first=self.app.context['game'].id; self.app.clear_selection(); self.app.random_button.invoke()
  self.wait(lambda:self.app.context is not None and self.app.context['game'].id!=first and not self.app.loading and not self.app.rendering and self.app.render_after is None)
  self.assertEqual(self.app.selected(),[]); self.assertEqual(self.app.random_button.master,self.app.preview_header)
  self.assertEqual(self.app.random_button.winfo_manager(),'pack'); self.assertFalse(self.app.random_button.place_info()); self.assertFalse((self.base/'Export').exists())
 def test_output_footer_updates_immediately_and_contains_no_report_instructions(self):
  output=self.base/'Chosen export'; self.app.output.set(str(output))
  self.assertEqual(self.app.export_status.get(),'Output folder: '+str(output))
  self.app.events.put(('done',([],str(output)))); self.wait(lambda:self.app.status.get().startswith('Finished'))
  self.assertEqual(self.app.export_status.get(),'Output folder: '+str(output)); self.assertNotIn('.csv',self.app.export_status.get())

 def test_two_progress_bars_are_persistent_before_during_and_after_preview(self):
  def visible_bars():
   self.assertEqual(self.app.progress.winfo_manager(),'pack')
   self.assertEqual(self.app.system_progress_frame.winfo_manager(),'pack')
   self.assertEqual(self.app.system_progress.winfo_manager(),'pack')
   self.assertIn('Overall progress',self.app.progress_text.get())
   self.assertIn('System progress',self.app.system_progress_text.get())
  visible_bars()
  self.app.loading=True; self.app.update_activity(); visible_bars()
  self.assertEqual(str(self.app.progress.cget('mode')),'indeterminate')
  self.assertEqual(float(self.app.system_progress.cget('value')),0)
  self.app.loading=False; self.app.update_activity(); visible_bars()
  self.app.selection_changed()
  self.wait(lambda:self.app.photo is not None and not self.app.loading and not self.app.rendering and self.app.render_after is None)
  visible_bars(); self.assertLessEqual(self.root.winfo_reqheight(),890)
  self.assertFalse((self.base/'Export').exists())
 def test_refresh_is_outside_artwork_and_both_bars_remain_during_file_operations(self):
  self.assertNotEqual(self.app.random_button.master,self.app.preview_frame)
  self.assertEqual(self.app.random_button.master,self.app.preview_header)
  self.assertEqual(self.app.random_button.cget('text'),'New preview')
  self.assertEqual(self.app.preview.winfo_parent(),str(self.app.preview_frame))
  self.app.job_kind='batch'; self.app.set_busy(True)
  self.app.show_batch_progress({'phase':'preparing'})
  self.assertEqual(self.app.system_progress_frame.winfo_manager(),'pack')
  for mode in ['install','restore']:
   self.app.show_operation_progress({'done':1,'total':2,'title':'Example','mode':mode})
   self.assertEqual(self.app.system_progress_frame.winfo_manager(),'pack')
   self.assertEqual(float(self.app.progress.cget('value')),50)
   self.assertEqual(float(self.app.system_progress.cget('value')),0)
  self.app.set_busy(False)

if __name__=='__main__': unittest.main()
