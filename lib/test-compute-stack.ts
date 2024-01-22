import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';


export interface TestComputeStackProps extends cdk.StackProps {
    vpc: ec2.Vpc;
    ec2SG: ec2.SecurityGroup;
}


export class TestComputeStack extends cdk.Stack {
  readonly jumpHostSG: ec2.SecurityGroup;

  constructor(scope: Construct, id: string, props: TestComputeStackProps) {
    super(scope, id, props);


    const userData = ec2.UserData.forLinux();
    userData.addCommands(
      'apt-get update -y',
      'apt-get install -y git awscli ec2-instance-connect',
      'apt install -y fish'
    );

    const machineImage = ec2.MachineImage.fromSsmParameter(
      '/aws/service/canonical/ubuntu/server/focal/stable/current/amd64/hvm/ebs-gp2/ami-id',
    );

    const jumpHostRole = new iam.Role(this, 'jumpHostRole', {
        assumedBy: new iam.CompositePrincipal(
          new iam.ServicePrincipal('ec2.amazonaws.com'),
        ),
        managedPolicies: [
          iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore'),
          iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonS3FullAccess'),
          iam.ManagedPolicy.fromAwsManagedPolicyName('AdministratorAccess'),
        ]
      });

    const instanceProf = new iam.CfnInstanceProfile(this, 'jumpHostInstanceProf', {
        roles: [jumpHostRole.roleName]
    });

    // // to test locally (streamlit)
    // jumpHostSecurityGroup.addIngressRule(
    //   ec2.Peer.anyIpv4(),
    //   ec2.Port.tcp(8501),
    //   'Streamlit default port'
    // );
    // this.jumpHostSG = jumpHostSecurityGroup;

    const ec2JumpHost = new ec2.Instance(this, 'ec2JumpHost', {
      vpc: props.vpc,
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.T2, ec2.InstanceSize.MICRO),
      machineImage: machineImage,
      securityGroup: props.ec2SG,
      userData: userData,
      role: jumpHostRole,
      requireImdsv2: true,
      // for public access testing
      vpcSubnets: {subnetType: ec2.SubnetType.PUBLIC},
      // for public access testing
      associatePublicIpAddress: true,
      blockDevices: [
          {
              deviceName: '/dev/sda1',
              mappingEnabled: true,
              volume: ec2.BlockDeviceVolume.ebs(128, {
                  deleteOnTermination: true,
                  encrypted: true,
                  volumeType: ec2.EbsDeviceVolumeType.GP2
              })
          }
      ]
  });

  }
}
