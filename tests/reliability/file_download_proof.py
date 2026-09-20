"""Run with python -m tests.reliability.file_download_proof in an isolated environment."""
import json
from pathlib import Path
import tempfile

import sarracenia
from sarracenia.config import no_file_config
from sarracenia.flow import Flow


def run_case(root: Path, announced: int, accept_wrong: bool) -> dict:
    source = root / 'source'
    destination = root / 'destination'
    source.mkdir(parents=True)
    destination.mkdir()
    payload = b'0123456789'
    (source / 'data.bin').write_bytes(payload)
    configuration = root / 'subscribe.conf'
    configuration.write_text(
        'download True\nmirror False\ninflight None\naccelThreshold 0\n'
        'permCopy False\nacceptSizeWrong ' + str(accept_wrong) + '\n'
        'directory ' + str(destination) + '\naccept .*\n')
    cfg = no_file_config()
    cfg.component = 'subscribe'
    cfg.config = 'file-length-proof'
    cfg.no = 1
    cfg.logLevel = 'info'
    cfg.pid_filename = str(root / 'instance.pid')
    cfg.metricsFilename = str(root / 'metrics.json')
    cfg.novipFilename = str(root / 'novip')
    cfg.parse_file(str(configuration), component='subscribe')
    flow = Flow(cfg)
    flow.have_vip = True
    message = sarracenia.Message()
    message.update(baseUrl='file:' + str(source), relPath='data.bin',
                   pubTime=sarracenia.nowstr(), size=announced, local_offset=0,
                   identity={'method': 'arbitrary', 'value': 'fixture'})
    flow.worklist.incoming = [message]
    flow.filter()
    assert len(flow.worklist.incoming) == 1, 'fixture did not reach the download phase'
    flow.work()
    output = destination / 'data.bin'
    actual = output.read_bytes() if output.exists() else None
    return dict(announced_size=announced, acceptSizeWrong=accept_wrong,
                source_bytes=len(payload), destination_hex=actual.hex() if actual is not None else None,
                destination_bytes=len(actual) if actual is not None else None,
                accepted=len(flow.worklist.ok), failed=len(flow.worklist.failed),
                rejected=len(flow.worklist.rejected),
                accepted_truncated=bool(flow.worklist.ok) and actual != payload,
                configuration=configuration.read_text())


def main() -> None:
    with tempfile.TemporaryDirectory(prefix='sr-file-length-') as directory:
        root = Path(directory)
        cases = [run_case(root / str(index), announced, allow)
                 for index, (announced, allow) in enumerate(((10, False), (3, False), (3, True)))]
    print(json.dumps(dict(module=sarracenia.__file__, cases=cases), indent=2))
    assert not any(case['accepted_truncated'] for case in cases), 'ordinary transfer accepted truncated bytes'


if __name__ == '__main__':
    main()
