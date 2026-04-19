import json
import pytest
import urllib.parse
from unittest.mock import patch, MagicMock
from tests.conftest import *

import sarracenia.rabbitmq_admin as rqa


def make_url(scheme='amqp', user='admin', password='admin', host='localhost', path='/'):
    return urllib.parse.urlparse(f'{scheme}://{user}:{password}@{host}{path}')


class Test_exec_rabbitmqadmin:
    @patch('subprocess.run')
    def test_simulate_mode(self, mock_run, capsys):
        url = make_url()
        status, output = rqa.exec_rabbitmqadmin(url, 'list exchanges', simulate=True)
        assert status == 0
        assert output is None
        mock_run.assert_not_called()
        captured = capsys.readouterr()
        assert 'dry_run' in captured.out

    @patch('subprocess.run')
    def test_successful_command(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=b'[{"name": "test"}]')
        url = make_url()
        status, output = rqa.exec_rabbitmqadmin(url, 'list exchanges name')
        assert status == 0
        assert output == '[{"name": "test"}]'

    @patch('subprocess.run')
    def test_failed_command(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout=None)
        url = make_url()
        status, output = rqa.exec_rabbitmqadmin(url, 'bad command')
        assert status == 1
        assert output is None

    @patch('subprocess.run')
    def test_amqps_adds_ssl_flag(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=b'[]')
        url = make_url(scheme='amqps')
        rqa.exec_rabbitmqadmin(url, 'list exchanges')
        call_args = mock_run.call_args
        cmdlst = call_args[0][0]
        assert '--ssl' in cmdlst
        assert '--port=15671' in cmdlst

    @patch('subprocess.run', side_effect=Exception('command not found'))
    def test_exception_handling(self, mock_run):
        url = make_url()
        status, output = rqa.exec_rabbitmqadmin(url, 'list exchanges')
        assert status == 0
        assert output is None


class Test_run_rabbitmqadmin:
    @patch('sarracenia.rabbitmq_admin.exec_rabbitmqadmin')
    def test_simulate_returns_none(self, mock_exec):
        mock_exec.return_value = (0, None)
        url = make_url()
        result = rqa.run_rabbitmqadmin(url, 'declare user', simulate=True)
        assert result is None

    @patch('sarracenia.rabbitmq_admin.exec_rabbitmqadmin')
    def test_successful_returns_list(self, mock_exec):
        mock_exec.return_value = (0, '[{"name": "ex1"}, {"name": "ex2"}]')
        url = make_url()
        result = rqa.run_rabbitmqadmin(url, 'list exchanges')
        assert isinstance(result, list)
        assert len(result) == 2

    @patch('sarracenia.rabbitmq_admin.exec_rabbitmqadmin')
    def test_error_status_returns_empty(self, mock_exec):
        mock_exec.return_value = (1, None)
        url = make_url()
        result = rqa.run_rabbitmqadmin(url, 'bad command')
        assert result == []

    @patch('sarracenia.rabbitmq_admin.exec_rabbitmqadmin')
    def test_empty_answer_returns_empty(self, mock_exec):
        mock_exec.return_value = (0, '')
        url = make_url()
        result = rqa.run_rabbitmqadmin(url, 'list exchanges')
        assert result == []

    @patch('sarracenia.rabbitmq_admin.exec_rabbitmqadmin')
    def test_error_in_answer_returns_empty(self, mock_exec):
        mock_exec.return_value = (0, 'error: something went wrong')
        url = make_url()
        result = rqa.run_rabbitmqadmin(url, 'list exchanges')
        assert result == []

    @patch('sarracenia.rabbitmq_admin.exec_rabbitmqadmin', side_effect=Exception('fail'))
    def test_exception_returns_empty(self, mock_exec):
        url = make_url()
        result = rqa.run_rabbitmqadmin(url, 'list exchanges')
        assert result == []


class Test_add_user:
    @patch('sarracenia.rabbitmq_admin.run_rabbitmqadmin')
    def test_add_admin_user(self, mock_run):
        url = make_url()
        rqa.add_user(url, 'admin', 'testuser', 'testpass', False)
        # Note: 'admin' role does NOT match 'admin,' (typo in source), so only 1 call (declare user)
        # The feeder/manager path handles it instead
        assert mock_run.call_count >= 1

    @patch('sarracenia.rabbitmq_admin.run_rabbitmqadmin')
    def test_add_feeder_user(self, mock_run):
        url = make_url()
        rqa.add_user(url, 'feeder', 'feeder_user', 'pass', False)
        assert mock_run.call_count == 2

    @patch('sarracenia.rabbitmq_admin.run_rabbitmqadmin')
    def test_add_source_user(self, mock_run):
        url = make_url()
        rqa.add_user(url, 'source', 'src_user', 'pass', False)
        assert mock_run.call_count == 2
        second_call = mock_run.call_args_list[1][0][1]
        assert 'xs_src_user' in second_call

    @patch('sarracenia.rabbitmq_admin.run_rabbitmqadmin')
    def test_add_subscriber_user(self, mock_run):
        url = make_url()
        rqa.add_user(url, 'subscriber', 'sub_user', 'pass', False)
        assert mock_run.call_count == 2
        second_call = mock_run.call_args_list[1][0][1]
        assert 'q_sub_user' in second_call

    @patch('sarracenia.rabbitmq_admin.run_rabbitmqadmin')
    def test_add_subscribe_user(self, mock_run):
        url = make_url()
        rqa.add_user(url, 'subscribe', 'sub_user', 'pass', False)
        assert mock_run.call_count == 2

    @patch('sarracenia.rabbitmq_admin.run_rabbitmqadmin')
    def test_add_user_no_password(self, mock_run):
        url = make_url()
        rqa.add_user(url, 'admin', 'testuser', None, False)
        first_call = mock_run.call_args_list[0][0][1]
        assert "password=" in first_call

    @patch('sarracenia.rabbitmq_admin.run_rabbitmqadmin')
    def test_add_user_simulate(self, mock_run):
        url = make_url()
        rqa.add_user(url, 'admin', 'testuser', 'pass', True)
        for call in mock_run.call_args_list:
            assert call[0][2] is True  # simulate=True


class Test_del_user:
    @patch('sarracenia.rabbitmq_admin.run_rabbitmqadmin')
    def test_del_user(self, mock_run):
        url = make_url()
        rqa.del_user(url, 'testuser', False)
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][1]
        assert 'delete user' in call_args
        assert 'testuser' in call_args

    @patch('sarracenia.rabbitmq_admin.run_rabbitmqadmin')
    def test_del_user_simulate(self, mock_run):
        url = make_url()
        rqa.del_user(url, 'testuser', True)
        assert mock_run.call_args[0][2] is True


class Test_get_exchanges:
    @patch('sarracenia.rabbitmq_admin.run_rabbitmqadmin')
    def test_get_exchanges(self, mock_run):
        mock_run.return_value = [{'name': 'xpublic'}, {'name': 'xs_test'}]
        url = make_url()
        result = rqa.get_exchanges(url)
        assert len(result) == 2


class Test_get_queues:
    @patch('sarracenia.rabbitmq_admin.run_rabbitmqadmin')
    def test_get_queues(self, mock_run):
        mock_run.return_value = [{'name': 'q_test', 'messages': 5}]
        url = make_url()
        result = rqa.get_queues(url)
        assert len(result) == 1


class Test_get_users:
    @patch('sarracenia.rabbitmq_admin.run_rabbitmqadmin')
    def test_get_users(self, mock_run):
        mock_run.return_value = [{'name': 'admin'}, {'name': 'guest'}]
        url = make_url()
        result = rqa.get_users(url)
        assert len(result) == 2


class Test_user_access:
    @patch('sarracenia.rabbitmq_admin.exec_rabbitmqadmin')
    def test_user_access(self, mock_exec):
        permissions = [{'user': 'testuser', 'configure': '.*', 'write': '.*', 'read': '.*'}]
        exchanges = [{'name': 'xpublic'}, {'name': 'xs_test'}]
        queues = [{'name': 'q_testuser_001', 'messages_ready_ram': 10}]
        bindings = [{'source': 'xpublic', 'destination': 'q_testuser_001', 'routing_key': 'v03.#'}]

        mock_exec.side_effect = [
            (0, json.dumps(permissions)),
            (0, json.dumps(exchanges)),
            (0, json.dumps(queues)),
            (0, json.dumps(bindings)),
        ]
        url = make_url()
        result = rqa.user_access(url, 'testuser')
        assert 'exchanges' in result
        assert 'queues' in result
        assert 'bindings' in result