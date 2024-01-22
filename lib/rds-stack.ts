import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as rds from "aws-cdk-lib/aws-rds";


export interface RDSStackProps extends cdk.StackProps {
    vpc: ec2.Vpc;
    sgLambda: ec2.SecurityGroup;
    sgEc2: ec2.SecurityGroup;
    ecsSecGroup: ec2.SecurityGroup;
  }

export class RDSStack extends cdk.Stack {
  readonly rdsDBName: string;
  readonly dbInstance: rds.DatabaseInstance;

  constructor(scope: Construct, id: string, props: RDSStackProps) {
    super(scope, id, props);
    
    // passed in as property
    const vpc = props.vpc;

    // extract the ID from the default security group object ... might not be needed
    // const vpcDefaultSecGroupId = ec2.SecurityGroup.fromSecurityGroupId(this, 'defaultSG', vpc.vpcDefaultSecurityGroup);

    // create RDS bits (security group and serverless instance)
    const dbName = "postgres";
    const rdsSecGroupName = "rds-security-group";

    const rdsSecurityGroup = new ec2.SecurityGroup(this, rdsSecGroupName, {
      securityGroupName: rdsSecGroupName,
      vpc: vpc,
      allowAllOutbound: false,
    });
    // this might not be needed
    // rdsSecurityGroup.connections.allowFrom(vpcDefaultSecGroupId, ec2.Port.tcp(5432));
    // allow connection from lambda
    rdsSecurityGroup.connections.allowFrom(props.sgLambda, ec2.Port.tcp(5432));
    // allow connection from test ec2 instance (will be deleted)
    rdsSecurityGroup.connections.allowFrom(props.sgEc2, ec2.Port.tcp(5432));
    // allow connection from ecs Task Security Group
    rdsSecurityGroup.connections.allowFrom(props.ecsSecGroup, ec2.Port.tcp(5432));

    const rdsInstance = new rds.DatabaseInstance(this, 'rdsInstance', {
        engine: rds.DatabaseInstanceEngine.POSTGRES,
        credentials: rds.Credentials.fromGeneratedSecret('postgres'),
        vpc: vpc,
        securityGroups: [rdsSecurityGroup],
    });
    this.dbInstance = rdsInstance;

    this.rdsDBName = dbName;
  }
}
