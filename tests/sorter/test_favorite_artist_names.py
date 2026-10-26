"""Tag sorting leaves files named after a favorite artist to the artist scan.

Otherwise the two fight over a file whose e621 artist differs from its name (tag sort moves it
out of the artist folder, the artist scan moves it back), every run.
"""
from fff.sorter.features.tag_sorter import TagSorter
from fff.sorter.handlers.artist_manager import artist_alias_in_filename


def _sorter(aliases):
    sorter = object.__new__(TagSorter)
    sorter.favorite_artist_aliases = aliases
    return sorter


def test_files_named_after_favorite_artists_are_left_alone(capsys):
    found = {
        'orf-explicit-4748052.jpg': 'P:/Archive/- Archived Favorites/Artists/Orf/orf-explicit-4748052.jpg',
        'floparts-explicit-1.jpg': 'P:/IPL/- Need to be Sorted/floparts-explicit-1.jpg',
        'morfin-explicit-2.jpg': 'P:/IPL/- Need to be Sorted/morfin-explicit-2.jpg',
    }
    _sorter(['orf', 'orf_nite'])._drop_files_named_after_favorite_artists(found)
    assert sorted(found) == ['floparts-explicit-1.jpg', 'morfin-explicit-2.jpg']
    assert 'Skipping 1 file(s)' in capsys.readouterr().out


def test_matching_is_the_artist_scans():
    assert artist_alias_in_filename('orf-explicit-1.jpg', 'orf')
    assert artist_alias_in_filename('fenn_perrox-safe-1.png', 'fenn.perrox')
    assert not artist_alias_in_filename('morfin-explicit-2.jpg', 'orf')
