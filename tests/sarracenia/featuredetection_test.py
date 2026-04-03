import pytest

from sarracenia.featuredetection import features


# ── features dict structure ──────────────────────────────────────────────

class Test_features_structure:
    def test_features_is_dict(self):
        assert isinstance(features, dict)

    def test_features_has_expected_keys(self):
        expected = ['amqp', 'mqtt', 'sftp', 'ftppoll', 'vip', 'watch',
                    'filetypes', 's3', 'process', 'retry', 'redis',
                    'reassembly', 'humanize', 'xattr']
        for key in expected:
            assert key in features, f"Missing feature key: {key}"

    def test_each_feature_has_required_fields(self):
        for name, feat in features.items():
            assert 'modules_needed' in feat, f"{name} missing modules_needed"
            assert 'present' in feat, f"{name} missing present flag"
            assert isinstance(feat['modules_needed'], list), f"{name} modules_needed not a list"
            assert isinstance(feat['present'], bool), f"{name} present is not bool"

    def test_each_feature_has_lament_and_rejoice(self):
        for name, feat in features.items():
            assert 'lament' in feat or 'Needed' in feat, f"{name} missing lament/Needed"
            assert 'rejoice' in feat or 'Needed' in feat, f"{name} missing rejoice/Needed"


# ── feature presence detection ───────────────────────────────────────────

class Test_feature_presence:
    def test_sftp_present_when_paramiko_available(self):
        """paramiko is installed in test env, so sftp should be present."""
        assert features['sftp']['present'] is True

    def test_amqp_feature_present(self):
        """amqp package is installed in test env."""
        assert features['amqp']['present'] is True

    def test_mqtt_feature_present(self):
        """paho.mqtt is installed in test env."""
        assert features['mqtt']['present'] is True

    def test_process_feature_present(self):
        """psutil is installed in test env."""
        assert features['process']['present'] is True

    def test_retry_feature_present(self):
        """jsonpickle is installed in test env."""
        assert features['retry']['present'] is True

    def test_watch_feature_present(self):
        """watchdog is installed in test env."""
        assert features['watch']['present'] is True

    def test_modules_needed_are_strings(self):
        for name, feat in features.items():
            for mod in feat['modules_needed']:
                assert isinstance(mod, str), f"{name}: module {mod} is not a string"