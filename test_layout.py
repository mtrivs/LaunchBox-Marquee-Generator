import copy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from PIL import Image,ImageDraw
import marquee as m

class LayoutTests(unittest.TestCase):
 def test_all_platforms_share_every_slot(self):
  data=m.load_profiles()
  for name,profile in list(data['platforms'].items())+[('generic',data['default'])]:
   for element in profile['elements']:
    for field,value in data['layout'][element['Tag']].items():
     self.assertEqual(element[field],value,(name,element['Tag'],field))
  for profile in list(data['platforms'].values())+[data['default']]:
   layers=[e for e in profile['elements'] if e.get('Tag') in ['Logo','Logo glow','Logo glow 2','Logo outline']]
   slots={tuple(e[k] for k in ['X','Y','W','H','HorizontalAlignment','VerticalAlignment']) for e in layers}
   self.assertEqual(len(slots),1)
 def test_padding_and_faint_fringe_are_trimmed(self):
  source=Image.new('RGBA',(200,100)); ImageDraw.Draw(source).rectangle((60,20,99,79),fill='red')
  source.putpixel((0,0),(255,0,0,2))
  self.assertEqual(m.trim_transparency(source).size,(40,60))
  self.assertEqual(m.trim_transparency(Image.new('RGBA',(50,50))).size,(1,1))
 def test_rendered_console_centers_for_different_shapes(self):
  data=m.load_profiles()
  with TemporaryDirectory(dir=Path(__file__).parent) as temporary:
   for i,(w,h,padx,pady) in enumerate([(120,80,10,30),(80,120,80,5),(300,30,1,100)]):
    path=Path(temporary)/f'{i}.png'
    source=Image.new('RGBA',(w+padx+40,h+pady+10))
    ImageDraw.Draw(source).rectangle((padx,pady,padx+w-1,pady+h-1),fill='red'); source.save(path)
    profile=copy.deepcopy(data['platforms'][['Sega 32X','Nintendo Entertainment System','Sony Playstation'][i]])
    element=next(e for e in profile['elements'] if e['Tag']=='Console Image')
    element.pop('Asset',None); element['EffectType']='None'
    profile['elements']=[element]; profile['accent_stripe']=False
    result=m.render(None,m.Game('Test','Test'),profile,{'Console image':path})
    mask=result.getchannel('R').point(lambda v:255 if v>200 else 0)
    x1,y1,x2,y2=mask.getbbox()
    self.assertLessEqual(abs((x1+x2)/2-1628.5),0.5)
    self.assertLessEqual(abs((y1+y2)/2-180),0.5)
    self.assertLessEqual(x2-x1,327); self.assertLessEqual(y2-y1,201)
    self.assertLess(abs((x2-x1)/(y2-y1)-w/h),0.4)
 def test_padding_does_not_change_fitted_visible_art(self):
  source=Image.new('RGBA',(20,30),'red')
  padded=Image.new('RGBA',(140,90)); padded.alpha_composite(source,(90,10))
  direct=m.fit_image(m.trim_transparency(source),327,201)
  trimmed=m.fit_image(m.trim_transparency(padded),327,201)
  self.assertEqual(direct.tobytes(),trimmed.tobytes())
 def test_fold_defaults_to_paper_and_override_wins(self):
  self.assertEqual(m.page_color_for({'main':'#C9C8CF','accent':'#78649F'}),'#C9C8CF')
  self.assertEqual(m.page_color_for({'main':'#FAC52B','accent':'#F68936'}),'#FAC52B')
  self.assertEqual(m.page_color_for({'main':'#000000','accent':'#2675C8'}),'#000000')
  self.assertEqual(m.page_color_for({'main':'#000000','accent':'#2675C8','page_color':'#00FF00'}),'#00FF00')
 def test_curl_color_preserves_alpha_and_shading(self):
  source=Image.new('RGBA',(2,1)); source.putpixel((0,0),(100,100,100,125)); source.putpixel((1,0),(240,240,240,255))
  result=m.colorize_page(source,'#CC2222')
  dark,light=result.getpixel((0,0)),result.getpixel((1,0))
  self.assertEqual([dark[3],light[3]],[125,255])
  self.assertGreater(light[0],dark[0]); self.assertGreater(light[0],light[1]*3)
 def test_curl_is_colorized_when_old_tint_switch_is_off(self):
  with TemporaryDirectory(dir=Path(__file__).parent) as temporary:
   path=Path(temporary)/'curl.png'; Image.new('RGBA',(10,10),'white').save(path)
   element={'UIType':'Image','Tag':'Page Curl','W':'10','H':'10','X':'1800','Y':'50','ImageValueType':'Image','Asset':str(path),'UseColorTint':'False'}
   profile={'main':'#CCCCCC','page_color':'#CC2222','accent':'#CC2222','elements':[element],'accent_stripe':False}
   result=m.render(None,m.Game('Test','Test'),profile,{})
   self.assertEqual(result.getpixel((1805,55)),(204,34,34))

 def test_rotated_foreground_slots_stay_inside_canvas(self):
  import math
  layout=m.load_profiles()['layout']
  for tag in ['Console Image','box 3d','cart','Logo','Platform Clear Art']:
   slot=layout[tag]
   x,y,w,h=[float(slot[k]) for k in ['X','Y','W','H']]
   angle=math.radians(float(slot.get('Angle',0)))
   rw=abs(w*math.cos(angle))+abs(h*math.sin(angle))
   rh=abs(w*math.sin(angle))+abs(h*math.cos(angle))
   cx,cy=x+w/2,y+h/2
   self.assertGreaterEqual(cx-rw/2,0,tag); self.assertLessEqual(cx+rw/2,1920,tag)
   self.assertGreaterEqual(cy-rh/2,0,tag); self.assertLessEqual(cy+rh/2,360,tag)

 def test_full_corner_logo_slot_fits_inside_paper(self):
  from PIL import ImageChops
  data=m.load_profiles()
  for name,profile in list(data['platforms'].items())+[('generic',data['default'])]:
   element=next(e for e in profile['elements'] if e['Tag']=='Platform Clear Art')
   w,h=round(float(element['W'])),round(float(element['H']))
   layer=m.position_layer(Image.new('RGBA',(w,h),'white'),element)
   outside=ImageChops.subtract(layer.getchannel('A'),m.corner_paper_mask(profile))
   self.assertIsNone(outside.getbbox(),name)
 def test_rendered_logo_and_shadow_do_not_cross_paper_edge(self):
  from PIL import ImageChops
  data=m.load_profiles()
  profile=copy.deepcopy(data['platforms']['Nintendo Entertainment System'])
  profile.update(main='#000000',accent_stripe=False)
  profile['elements']=[e for e in profile['elements'] if e['Tag'] in ['Platform Clear Art','Page curl background']]
  logo=next(e for e in profile['elements'] if e['Tag']=='Platform Clear Art')
  logo.update(EffectColor='White',EffectOpacity='1',EffectBlurRadius='20')
  with TemporaryDirectory(dir=Path(__file__).parent) as temporary:
   path=Path(temporary)/'logo.png'; Image.new('RGBA',(400,100),'red').save(path)
   rendered=m.render(None,m.Game('Test','Test'),profile,{'Platform clear logo':path})
   visible=rendered.getchannel('R').point(lambda v:255 if v else 0)
   self.assertIsNone(ImageChops.subtract(visible,m.corner_paper_mask(profile)).getbbox())
   self.assertIsNotNone(visible.getbbox())
 def test_hardware_gray_palettes_and_sega_styles(self):
  profiles=m.load_profiles()['platforms']
  for name,expected in [('Sony Playstation','#A7A8A3'),('Nintendo Entertainment System','#424448')]:
   self.assertEqual(profiles[name]['main'],expected)
   self.assertEqual(m.page_color_for(profiles[name]),expected)
  self.assertEqual(profiles['Sega Genesis']['main'],'#EB2A47')
  self.assertEqual(profiles['Sega 32X']['main'],'#FAC52B')
  for name in ['Sega Genesis','Sega 32X']:
   self.assertTrue(any(e.get('CornerSliver') and e.get('Asset') for e in profiles[name]['elements']))

 def test_cartridge_layers_above_box_for_every_system(self):
  data=m.load_profiles()
  for name,profile in list(data['platforms'].items())+[('generic',data['default'])]:
   box=next(e for e in profile['elements'] if e['Tag']=='box 3d')
   cart=next((e for e in profile['elements'] if e['Tag']=='cart'),None)
   if cart is None: continue
   self.assertGreater(float(cart['Zorder']),float(box['Zorder']),name)
 def test_cartridge_covers_box_at_overlap(self):
  data=m.load_profiles()
  with TemporaryDirectory(dir=Path(__file__).parent) as temporary:
   box_path=Path(temporary)/'box.png'; cart_path=Path(temporary)/'cart.png'
   Image.new('RGBA',(288,320),'red').save(box_path)
   Image.new('RGBA',(217,191),'blue').save(cart_path)
   for name in ['Sega 32X','Nintendo Entertainment System','Sony Playstation']:
    profile=copy.deepcopy(data['platforms'][name])
    profile['elements']=[e for e in reversed(profile['elements']) if e['Tag'] in ['box 3d','cart']]
    for element in profile['elements']: element['EffectType']='None'
    rendered=m.render(None,m.Game('Test',name),profile,{'Game box 3D':box_path,'Game cart front':cart_path})
    self.assertEqual(rendered.getpixel((300,200)),(0,0,255),name)
    self.assertEqual(rendered.getpixel((100,100)),(255,0,0),name)
 def test_no_added_diagonal_color_bar(self):
  # An old saved profile flag cannot bring back the removed overlay.
  profile={'main':'#000000','accent':'#FF0000','accent_stripe':True,'elements':[]}
  rendered=m.render(None,m.Game('Test','Test'),profile,{})
  self.assertIsNone(rendered.getbbox())

 def test_every_detected_system_has_a_distinct_main_color(self):
  import json
  data=m.load_profiles()
  states=data['palette_systems']
  class Library:
   scrape_as={}
  colors=[m.profile_for(Library(),name,data)['main'].upper() for name in states]
  self.assertEqual(len(colors),51)
  self.assertEqual(len(set(colors)),51)
 def test_both_faces_and_reverse_shade_are_coordinated(self):
  data=m.load_profiles()
  for name,profile in list(data['platforms'].items())+[('generic',data['default'])]:
   self.assertEqual(m.page_color_for(profile).upper(),profile['main'].upper(),name)
   self.assertEqual(m.page_back_color_for(profile).upper(),m.shade_color(profile['main']).upper(),name)
 def test_legacy_red_accent_does_not_color_the_reverse(self):
  profile={'main':'#274A9C','accent':'#FF0000'}
  self.assertEqual(m.page_back_color_for(profile),m.shade_color('#274A9C'))
  profile['page_back_color']='#112233'
  self.assertEqual(m.page_back_color_for(profile),'#112233')
 def test_corner_art_recoloring_removes_baked_in_red(self):
  result=m.colorize_page(Image.new('RGBA',(3,3),'red'),'#0000FF')
  red,green,blue,alpha=result.getpixel((1,1))
  self.assertEqual((red,green,alpha),(0,0,255)); self.assertGreater(blue,0)
 def test_outline_keeps_brand_colors_and_improves_edge_contrast(self):
  for background,expected in [('#FFFFFF',(0,0,0)),('#000000',(255,255,255))]:
   layer=Image.new('RGBA',m.SIZE); ImageDraw.Draw(layer).rectangle((1880,30,1900,130),fill=(200,30,60,255))
   rendered=m.readable_logo(layer,{'main':background})
   self.assertEqual(rendered.getpixel((1890,80)),(200,30,60,255))
   self.assertEqual(rendered.getpixel((1879,80)),expected+(255,))
 def test_outline_color_contrasts_with_all_paper_palettes(self):
  for name,profile in m.load_profiles()['platforms'].items():
   self.assertGreaterEqual(m.contrast_ratio(m.logo_outline_color(profile),profile['main']),4.5,name)
 def test_rendered_reverse_ignores_old_red_tint(self):
  with TemporaryDirectory(dir=Path(__file__).parent) as temporary:
   path=Path(temporary)/'curl.png'; Image.new('RGBA',(10,10),'white').save(path)
   element={'UIType':'Image','Tag':'Page curl shadow','W':'10','H':'10','X':'1800','Y':'50','ImageValueType':'Image','Asset':str(path),'UseColorTint':'True','ColorTint':'#FF0000'}
   profile={'main':'#274A9C','accent':'#FF0000','elements':[element]}
   rendered=m.render(None,m.Game('Test','Test'),profile,{})
   self.assertEqual(rendered.getpixel((1805,55)),m.color(m.shade_color(profile['main']))[:3])

 def test_background_is_centered_globally(self):
  data=m.load_profiles()
  for name,profile in list(data['platforms'].items())+[('generic',data['default'])]:
   background=next(e for e in profile['elements'] if e['Tag']=='Fan Art')
   self.assertEqual(background['VerticalAlignment'],'Center',name)
   self.assertEqual(background['Stretch'],'UniformToFill',name)
 def test_background_render_shows_middle_third(self):
  data=m.load_profiles()
  profile=copy.deepcopy(data['platforms']['Nintendo Entertainment System'])
  profile['elements']=[e for e in profile['elements'] if e['Tag']=='Fan Art']
  profile['elements'][0].update(EffectType='None',Opacity='1')
  with TemporaryDirectory(dir=Path(__file__).parent) as temporary:
   path=Path(temporary)/'background.png'
   source=Image.new('RGBA',(1919,1080),'red'); draw=ImageDraw.Draw(source)
   draw.rectangle((0,360,1918,719),fill=(0,255,0)); draw.rectangle((0,720,1918,1079),fill='blue'); source.save(path)
   rendered=m.render(None,m.Game('Test','Test'),profile,{'Fan art':path})
   for y in [0,180,359]: self.assertEqual(rendered.getpixel((960,y)),(0,255,0))

if __name__=='__main__': unittest.main()

