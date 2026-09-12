#!/usr/bin/env python3
"""Build the actual shared PHP recipe and PrestaShop profile for image tests."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'docker')]
from create_project import scaffold
from project import Project


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('env_file', type=Path)
    parser.add_argument('--tag', default='setup-php-image-test')
    args = parser.parse_args()
    versions = args.env_file.read_text()
    scratch = ROOT / '.test-work'
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as directory:
        app = scaffold('php-image-ci', directory)
        with (app / 'env/common.env').open('a') as output:
            output.write('\n' + versions + '\n')
        project = Project(app)
        project.run(['build', 'php-fpm-base'], profiles='build_only')
        project.run(['build', 'php-fpm'])
        image = project.model()['services']['php-fpm']['image']
        subprocess.run(['docker', 'tag', image, args.tag], check=True)


if __name__ == '__main__':
    main()
