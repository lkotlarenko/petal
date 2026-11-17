"""Resolution policy, mixed local inputs, cached evidence and measured advice."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from petal import hardware_cache
from petal.capabilities import Capabilities
from petal.commands import encode_command
from petal.discovery import discover_selection, allocate_paths
from petal.media import target_size, target_sar
from petal.models import Settings, RESOLUTIONS, Job, Status
from petal.process import Runner
from petal.quality import model_for_size
from petal.recommend import recommend
from tests.test_core import media
from tests.test_integration import generate


class ResolutionTests(unittest.TestCase):
    def test_all_ceilings_orientation_no_upscale_and_aspect(self):
        for key, bounds in RESOLUTIONS.items():
            settings = Settings(resolution=key)
            for width, height in ((7680,4320),(4320,7680),(4096,4096),(640,360),(360,640),(853,479),(1440,1080)):
                source = media(width,height)
                w,h = target_size(source,settings)
                self.assertLessEqual(w,width)
                self.assertLessEqual(h,height)
                self.assertEqual(w%2+h%2,0)
                if bounds:
                    self.assertLessEqual(max(w,h),bounds[0])
                    self.assertLessEqual(min(w,h),bounds[1])
                self.assertAlmostEqual(w/h*float(target_sar(source,settings)),width/height,places=4)
        self.assertEqual(target_size(media(7680,4320),Settings(resolution='2160p')),(3840,2160))
        self.assertEqual(target_size(media(2160,3840),Settings(resolution='1440p')),(1440,2560))

    def test_quality_uses_actual_dimensions_not_ceiling(self):
        source=media(1280,720)
        low,high=Settings(resolution='720p'),Settings(resolution='2160p')
        self.assertEqual(low.encoding_key(source),high.encoding_key(source))
        for config in (low,high):
            args=encode_command(source,Path('/tmp/fixture.mkv'),config,hardware_decode=False)
            self.assertEqual(args[args.index('-global_quality:v:0')+1],'99')
        self.assertNotEqual(low.encoding_key(media(3840,2160)),high.encoding_key(media(3840,2160)))
        self.assertEqual(Settings(codec='hevc_vaapi').quality_for_size((3840,2160)),26)
        self.assertEqual(model_for_size((3840,2160)),'vmaf_4k_v0.6.1neg')


class CacheTests(unittest.TestCase):
    def test_cache_hit_and_configuration_and_binary_invalidation(self):
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)
            config=Settings()
            caps=Capabilities(config.device,tested={'av1_vaapi:Balanced:8':True})
            hardware_cache.save(directory,config,caps)
            self.assertTrue(hardware_cache.load(directory,config).supports(config))
            self.assertIsNone(hardware_cache.load(directory,replace(config,resolution='720p')))
            with patch('petal.hardware_cache.signature',return_value='changed ffmpeg or driver'):
                self.assertIsNone(hardware_cache.load(directory,config))
            hardware_cache.cache_path(directory,config).write_text('{broken')
            self.assertIsNone(hardware_cache.load(directory,config))


class DropTests(unittest.TestCase):
    def test_nested_unicode_duplicate_inputs_and_collisions(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); source=root/'猫'; source.mkdir(); (source/'nested').mkdir()
            first=source/'nested'/'one.mp4'; second=source/'nested'/'one.mov'
            # FFV1-in-MP4 is unsupported by older distribution FFmpeg builds.
            # Misnamed Matroska still tests content probing and name collisions.
            fixture=source/'nested'/'fixture.mkv'
            generate(fixture,160,90); fixture.rename(first)
            second.write_bytes(first.read_bytes())
            (source/'loop').symlink_to(source,target_is_directory=True)
            namespaces={}
            jobs=discover_selection([first,source],root/'output',Runner(),namespaces)
            self.assertEqual(len(jobs),2)
            self.assertTrue(all(j.relative.parent==Path('猫/nested') for j in jobs))
            self.assertEqual(len({j.destination for j in jobs}),2)
            self.assertFalse((root/'output').exists())

    def test_preferred_name_survives_later_collision(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); source=root/'source'; source.mkdir()
            first=source/'clip.mp4'; second=source/'clip.mov'
            first.touch();second.touch()
            output=root/'output'
            names=allocate_paths({first:Path('clip.mp4'),second:Path('clip.mov')},output,{'clip.mp4':output/'clip.mkv'})
            self.assertEqual(names[first],output/'clip.mkv')
            self.assertNotEqual(names[first],names[second])

    def test_no_output_yet_and_unsafe_folder_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); source=root/'source';source.mkdir()
            video=source/'clip.mkv';generate(video,160,90)
            jobs=discover_selection([video],None,Runner(),{})
            self.assertEqual(len(jobs),1)
            self.assertFalse(jobs[0].destination.is_absolute())
            with self.assertRaises(ValueError):
                discover_selection([source],source/'output',Runner(),{})
            # Individually selected files can use a sibling output folder in their parent.
            self.assertEqual(len(discover_selection([video],source/'output',Runner(),{})),1)

    def test_adding_parent_folder_keeps_existing_child_namespace_on_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);parent=root/'collection';child=parent/'series';child.mkdir(parents=True)
            video=child/'clip.mkv';generate(video,160,90)
            namespaces={}
            first=discover_selection([child],root/'output',Runner(),namespaces)[0]
            # Only persisted namespaces, no in-memory jobs, are needed to retain ownership.
            second=discover_selection([parent,child],root/'output',Runner(),dict(namespaces))[0]
            self.assertEqual(second.relative,first.relative)
            self.assertEqual(second.destination,first.destination)


class RecommendationTests(unittest.TestCase):
    def checked(self,mean=98,save=10,**values):
        return Job(Path('/source/x'),Path('x'),Path('/out/x'),status=Status.COMPLETED,size=1000,
                   final_size=1000-save*10,encoder_profile='Balanced',
                   quality={'mean':mean,'p10':mean-1,'worst_scene':mean-1,
                            'status':'Review suggested' if mean<90 else 'No concern in sampled scenes'},**values)

    def test_no_fabricated_advice_and_quality_has_priority(self):
        self.assertIsNone(recommend(Settings(),[]))
        advice=recommend(Settings(),[self.checked(mean=80)])
        self.assertEqual((advice.action,advice.value),('profile','High Quality'))
        self.assertIsNone(recommend(Settings(profile='High Quality'),[self.checked(mean=80)]))

    def test_evidence_drives_speed_and_savings_suggestions(self):
        jobs=[self.checked(encode_seconds=1,verify_seconds=3) for _ in range(2)]
        self.assertEqual(recommend(Settings(),jobs).action,'fast')
        advice=recommend(Settings(strict=False),jobs)
        self.assertEqual((advice.action,advice.value),('profile','Maximum Savings'))
        jobs[0].quality['geometry_note']='Decoded geometry differs'
        self.assertEqual(recommend(Settings(),jobs).action,'details')
