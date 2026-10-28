"""pre-commit check of a PrestaShop shop: one .gitignore, at the shop root (git_hooks.py runs it).

A module's own .gitignore (its author's) silently hides shop files the root .gitignore means to track: module
vendor/ parts, tests, mail templates, images. So the shop keeps every rule in its root .gitignore, anchored at the
shop root, and a commit that would hold another .gitignore stops. Composer packages inside a vendor/ folder keep
theirs: they are third-party code taken as it is.

Run by the hook in the work tree being committed: git ls-files then reads the index that commit uses (also a linked
worktree's, or the temporary one of git commit -a). --prefix names the shop folder when the shop is a folder of the
environment repository (e.g. public/); a shop repository of its own has none."""

import argparse
import subprocess
import sys


def nested_gitignores(prefix=''):
    """The shop's .gitignore files besides its root one that are or would be in the repository: in the index, or new
    and not ignored themselves; none inside a vendor/ folder."""
    result = subprocess.run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard', '--',
                             f':(glob){prefix}**/.gitignore'], capture_output=True)
    if result.returncode:
        raise ValueError('git ls-files failed: ' + result.stderr.decode(errors='replace').strip())
    names = sorted({name for name in result.stdout.decode(errors='replace').split('\0') if name})
    return [name for name in names if name != f'{prefix}.gitignore' and 'vendor' not in name.split('/')[:-1]]


def message(names, prefix=''):
    listed = ''.join(f'  {name}\n' for name in names)
    root = f'{prefix}.gitignore'
    return (f'Commit stopped: the shop keeps one .gitignore, at its root ({root}), but there are {len(names)} more:\n'
            f'{listed}'
            f'Merge their rules into {root}, anchored at the shop root (data/*.xml in modules/x/.gitignore becomes '
            f'/modules/x/data/*.xml), and tidy them: drop what {root} covers already and the rules that hide code '
            f'(vendor/, tests, mails, file types such as *.png). Then remove these files (git rm) and commit again. '
            f'Only vendor/ packages keep their own .gitignore. Nothing was committed.\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--prefix', default='', help='the shop folder inside the repository, e.g. public/')
    args = parser.parse_args(argv)
    try:
        names = nested_gitignores(args.prefix)
    except ValueError as error:
        print(f'gitignore check: {error}', file=sys.stderr)
        return 1
    if names:
        sys.stderr.write(message(names, args.prefix))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
