"""All AWS data and calls here are offline fixtures."""

import pytest

from ops.aws.verify_infra import CheckFailure, verify

INSTANCE = "i-" + "a" * 17


def fixture_call(service, operation, *args):
    responses = {
        "describe-instances": {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "State": {"Name": "running"},
                            "InstanceType": "t4g.medium",
                            "Architecture": "arm64",
                            "MetadataOptions": {
                                "HttpTokens": "required",
                                "HttpPutResponseHopLimit": 2,
                            },
                            "SecurityGroups": [{"GroupId": "sg-fixture"}],
                            "BlockDeviceMappings": [{"Ebs": {"VolumeId": "vol-fixture"}}],
                        }
                    ]
                }
            ]
        },
        "describe-security-groups": {
            "SecurityGroups": [
                {
                    "IpPermissions": [
                        {"IpProtocol": "tcp", "FromPort": p, "ToPort": p} for p in (80, 443)
                    ]
                }
            ]
        },
        "describe-instance-attribute": {"DisableApiTermination": {"Value": True}},
        "describe-volumes": {
            "Volumes": [
                {
                    "VolumeId": "vol-fixture",
                    "Encrypted": True,
                    "VolumeType": "gp3",
                    "Size": 30,
                    "Tags": [{"Key": "Backup", "Value": "yes"}],
                }
            ]
        },
        "describe-addresses": {"Addresses": [{"InstanceId": INSTANCE, "AssociationId": "fake"}]},
        "describe-instance-information": {
            "InstanceInformationList": [{"InstanceId": INSTANCE, "PingStatus": "Online"}]
        },
        "get-lifecycle-policies": {"Policies": [{"State": "ENABLED", "PolicyId": "fake"}]},
        "get-lifecycle-policy": {
            "Policy": {
                "State": "ENABLED",
                "PolicyDetails": {
                    "TargetTags": [{"Key": "Backup", "Value": "yes"}],
                    "Schedules": [{}],
                    "ResourceTypes": ["VOLUME"],
                },
            }
        },
    }
    return responses[operation]


def test_all_checks_pass_with_offline_fixture():
    assert all(passed for _, passed in verify(INSTANCE, fixture_call))


@pytest.mark.parametrize(
    "operation",
    [
        "describe-security-groups",
        "describe-volumes",
        "get-lifecycle-policies",
        "describe-instance-information",
        "describe-addresses",
    ],
)
def test_missing_or_unsafe_data_fails(operation):
    def call(service, name, *args):
        return {} if name == operation else fixture_call(service, name, *args)

    assert not all(passed for _, passed in verify(INSTANCE, call))


def test_ssh_rejected():
    def call(service, operation, *args):
        response = fixture_call(service, operation, *args)
        if operation == "describe-security-groups":
            response["SecurityGroups"][0]["IpPermissions"].append(
                {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22}
            )
        return response

    assert dict(verify(INSTANCE, call))["inbound TCP 80/443 only; no SSH"] is False


def test_invalid_id_never_calls_aws():
    def call(*args):
        pytest.fail("must not call AWS")

    with pytest.raises(CheckFailure):
        verify("invalid", call)
