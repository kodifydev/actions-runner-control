import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('docker_wrapper', Path(__file__).parents[1]/'infra/docker-wrapper.py')
assert spec and spec.loader
wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)


class DockerWrapperTests(unittest.TestCase):
    def test_rewrites_only_well_known_socket_source(self):
        args = ['create','-v','/var/run/docker.sock:/var/run/docker.sock:ro','image']
        self.assertEqual(wrapper.translate(args), ['create','-v','/run/kodify-ci/docker.sock:/var/run/docker.sock:ro','image'])
        self.assertEqual(args[2], '/var/run/docker.sock:/var/run/docker.sock:ro')

    def test_preserves_other_mounts_and_all_command_arguments(self):
        args = ['run','--volume','/data:/data','image','echo','/var/run/docker.sock:/x']
        self.assertEqual(wrapper.translate(args), args)

    def test_preserves_socket_paths_that_are_not_binds(self):
        args=['--host','unix:///var/run/docker.sock','info']
        self.assertEqual(wrapper.translate(args),args)
