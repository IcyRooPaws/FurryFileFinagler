import logging

from fff.common.logging import capture_print, setup_logging


def test_capture_print_logs_lines_and_keeps_console_output(tmp_path, capsys):
    root = logging.getLogger()
    saved = list(root.handlers), root.level
    try:
        log_file = setup_logging("fluffle", log_dir=tmp_path)
        logger = logging.getLogger("fff.fluffle.search")
        logger.info("before capture")
        with capture_print(logger):
            print("STEP 1: Database Connection")
            print()
            print("    [ERROR] e621 processing failed: boom")
            print("Warning: Could not retrieve processed images")
            print("no newline", end="")
        logging.getLogger("fff.fluffle").info("Moved a.png")
        for handler in root.handlers:
            handler.flush()
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        for handler in saved[0]:
            root.addHandler(handler)
        root.setLevel(saved[1])

    console = capsys.readouterr().out
    # print output reaches the console unchanged and only once
    assert "STEP 1: Database Connection\n\n    [ERROR] e621 processing failed: boom\n" in console
    assert console.count("STEP 1: Database Connection") == 1
    # ordinary log records still go to the console
    assert "INFO - before capture" in console
    assert "INFO - Moved a.png" in console

    log = log_file.read_text(encoding="utf-8").splitlines()
    assert any(l.endswith("fff.fluffle.search - INFO - STEP 1: Database Connection") for l in log)
    assert any(" - ERROR -     [ERROR] e621 processing failed: boom" in l for l in log)
    assert any(" - WARNING - Warning: Could not retrieve processed images" in l for l in log)
    assert any(l.endswith("INFO - no newline") for l in log)
    assert any(l.endswith("INFO - Moved a.png") for l in log)
    assert not any(l.endswith("INFO - ") for l in log)  # blank lines aren't logged


def test_logged_command_names_the_file_and_logs_crashes(tmp_path, monkeypatch):
    from fff.common import logging as fff_logging
    from fff.common import paths

    monkeypatch.setattr(paths, "LOG_DIR", tmp_path)
    root = logging.getLogger()
    saved = list(root.handlers), root.level

    @fff_logging.logged_command("fluffle", "process_fa", "fff.fluffle.fa_matches")
    def ok():
        print("FA posts processed: 2")
        return 7

    @fff_logging.logged_command("fluffle", "process_fa", "fff.fluffle.fa_matches")
    def crash():
        print("about to fail")
        raise RuntimeError("boom")

    try:
        assert ok() == 7
        try:
            crash()
            raise AssertionError("crash() should re-raise")
        except RuntimeError:
            pass
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        for handler in saved[0]:
            root.addHandler(handler)
        root.setLevel(saved[1])

    logs = sorted((tmp_path / "fluffle").glob("*_process_fa.log"))
    assert logs, "expected logs/fluffle/<stamp>_process_fa.log"
    text = "\n".join(p.read_text(encoding="utf-8") for p in logs)
    assert "INFO - FA posts processed: 2" in text
    assert "INFO - process_fa finished" in text
    assert "INFO - about to fail" in text
    assert "ERROR - process_fa failed" in text and "RuntimeError: boom" in text


def test_capture_print_survives_a_lost_console(tmp_path):
    from fff.common.logging import PrintToLog

    class DeadPipe:
        def write(self, text):
            raise OSError(22, "Invalid argument")

        def flush(self):
            raise OSError(22, "Invalid argument")

    root = logging.getLogger()
    saved = list(root.handlers), root.level
    try:
        log_file = setup_logging("fluffle", log_dir=tmp_path, console=False)
        tee = PrintToLog(DeadPipe(), logging.getLogger("fff.fluffle.search"))
        print("    Checking FA favorite status...", end=" ", flush=True, file=tee)
        print("[OK] Already favorited", file=tee)
        for handler in root.handlers:
            handler.flush()
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        for handler in saved[0]:
            root.addHandler(handler)
        root.setLevel(saved[1])

    log = log_file.read_text(encoding="utf-8")
    assert "Checking FA favorite status... [OK] Already favorited" in log
    assert log.count("Console output lost") == 1


def test_archive_setup_logging_writes_its_file_when_logging_is_already_configured(tmp_path):
    """A tool opened inside the Manager (root already has handlers) still gets a working log."""
    import logging

    from fff.archive import logging_setup

    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    outer = logging.StreamHandler()
    root.addHandler(outer)
    try:
        logging_setup.setup_logging(str(tmp_path / "a"))
        logging.info("first tool")
        logging_setup.setup_logging(str(tmp_path / "b"))   # a second tool replaces the first file
        logging.info("second tool")
        first, = (tmp_path / "a").glob("*.log")
        second, = (tmp_path / "b").glob("*.log")
        assert "first tool" in first.read_text(encoding="utf-8")
        assert "second tool" not in first.read_text(encoding="utf-8")
        assert "second tool" in second.read_text(encoding="utf-8")
    finally:
        if logging_setup._file_handler is not None:
            root.removeHandler(logging_setup._file_handler)
            logging_setup._file_handler.close()
            logging_setup._file_handler = None
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
