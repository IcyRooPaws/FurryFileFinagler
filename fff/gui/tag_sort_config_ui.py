# fff: implemented in media sorter/scripts/tag_sort_config_ui.py.
# fff: forwards to fff.gui.tag_sort_config_ui_v2; the __main__ block became parse_args + main.
# -*- coding: utf-8 -*-
"""Tag Sort Configuration UI -- shim (Phase 6)

The original v1 implementation has been superseded by the rebuilt tree-view UI.
This file forwards all entry points to tag_sort_config_ui_v2.

Original v1 source is preserved in tag_sort_config_ui_v1_backup.py.

Usage (unchanged):
    python scripts/tag_sort_config_ui.py [--config PATH]
    python main.py tag-sort-config
"""

from pathlib import Path

from fff.gui.tag_sort_config_ui_v2 import launch, DEFAULT_CONFIG_PATH


def parse_args(argv=None):  # fff: the original parsed args inside the __main__ block
    import argparse
    p = argparse.ArgumentParser(description='Tag Sort Config UI')
    p.add_argument('--config', default=str(DEFAULT_CONFIG_PATH),
                   help='Path to tag_sorting.yaml')
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    launch(config_path=Path(args.config))


if __name__ == '__main__':
    main()
