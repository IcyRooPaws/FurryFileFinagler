from fff.gui.manager_ui import PAGES


def test_manager_pages_and_actions_are_navigable():
    assert len(PAGES) >= 5
    assert len({page.title for page in PAGES}) == len(PAGES)
    for page in PAGES:
        assert page.title and page.detail
        assert page.actions
        for action in page.actions:
            assert action.title and action.detail
            assert action.command and all(isinstance(part, str) and part for part in action.command)


def test_manager_separates_read_only_and_changing_actions():
    actions = {action.title: action for page in PAGES for action in page.actions}

    assert not actions["Check setup"].changes_data
    assert not actions["Preview daily workflow"].changes_data
    assert actions["Run daily workflow"].changes_data
    assert actions["Review blacklisted posts"].changes_data


def test_manager_gallery_uses_local_only_url():
    gallery = next(action for page in PAGES for action in page.actions
                   if action.title == "Open archive gallery")
    assert gallery.browser_url == "http://127.0.0.1:5000"