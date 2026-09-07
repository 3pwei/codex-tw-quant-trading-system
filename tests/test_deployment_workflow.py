from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DeploymentWorkflowTests(unittest.TestCase):
    def test_remote_script_is_not_executed_from_stdin(self):
        workflow = (ROOT / ".github/workflows/deploy-lightsail.yml").read_text()
        self.assertNotIn("deploy.sh' | sudo bash -s", workflow)
        self.assertIn("> /tmp/tw-quant-deploy.sh", workflow)
        self.assertIn("sudo bash /tmp/tw-quant-deploy.sh", workflow)

    def test_deploy_recreates_and_verifies_running_images(self):
        script = (ROOT / "deploy/lightsail/deploy.sh").read_text()
        self.assertIn("run --rm --no-deps -T market-api", script)
        self.assertIn("--force-recreate", script)
        self.assertIn("docker image inspect --format '{{.Id}}'", script)
        self.assertIn("docker inspect --format '{{.Image}}'", script)


if __name__ == "__main__":
    unittest.main()
