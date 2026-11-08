"""tools/package_skill.py builds a valid .skill archive.  Run: python -m unittest discover -s tests"""
import importlib.util
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_skill", ROOT / "tools" / "package_skill.py")
package_skill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package_skill)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_real_skill_passes_its_checks(self):
        self.assertEqual(package_skill.check_skill(package_skill.SKILL_DIR), [])

    def test_archive_layout(self):
        out = self.tmp / "ticket-creator.skill"
        count = package_skill.build(package_skill.SKILL_DIR, out)
        names = zipfile.ZipFile(out).namelist()
        self.assertEqual(len(names), count)
        self.assertIn("ticket-creator/SKILL.md", names)
        self.assertIn("ticket-creator/scripts/tickets.py", names)
        self.assertIn("ticket-creator/references/platforms/github.md", names)
        self.assertTrue(all(n.startswith("ticket-creator/") for n in names))
        self.assertFalse(any("__pycache__" in n or n.endswith(".pyc") for n in names))

    def test_bad_frontmatter_is_rejected(self):
        skill = self.tmp / "my-skill"
        skill.mkdir()
        (skill / "SKILL.md").write_text("---\nname: other-name\ndescription: " + "x" * 1100 + " <tag>\n---\n",
                                        encoding="utf-8")
        problems = "\n".join(package_skill.check_skill(skill))
        self.assertIn("doesn't match the folder name", problems)
        self.assertIn("limit is 1024", problems)
        self.assertIn("angle brackets", problems)
        with self.assertRaises(SystemExit):
            package_skill.build(skill, self.tmp / "x.skill")


if __name__ == "__main__":
    unittest.main()
