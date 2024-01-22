import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as lambda from "aws-cdk-lib/aws-lambda";
import path = require("path");
import * as rds from 'aws-cdk-lib/aws-rds';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import { SqsEventSource } from 'aws-cdk-lib/aws-lambda-event-sources';
import * as events from 'aws-cdk-lib/aws-events';
import  * as targets from 'aws-cdk-lib/aws-events-targets';


export interface RdsDdlAutomationStackProps extends cdk.StackProps {
  ddlTriggerQueue: sqs.Queue;
  rdsInstance: rds.DatabaseInstance;
  dbName: string;
  ddlSourceS3Bucket: s3.Bucket;
  vpc: ec2.Vpc;
  lambdaSG: ec2.SecurityGroup;
  ddlSourceStackName: string;
}

export class RdsDdlAutomationStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: RdsDdlAutomationStackProps) {
    super(scope, id, props);

    // setting some constants
    const ddlTriggerQueue = props.ddlTriggerQueue;
    const rdsInstance = props.rdsInstance;
    const dbName = props.dbName;
    const sourceS3Bucket = props.ddlSourceS3Bucket;
    const ddlSourceStackName = props.ddlSourceStackName;

    // private subnets
    const privSubnets = props.vpc.selectSubnets({subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS});

    // capturing architecture for docker container (arm or x86)
    const dockerPlatform = process.env["DOCKER_CONTAINER_PLATFORM_ARCH"]

    // Docker assets for init lambda function
    const deployDockerfile = path.join(__dirname, "../lambda/rds-ddl-init/");

    // lambda function to deploy DDL on RDS (when it is first created)
    const ddlInitDeployFn = new lambda.Function(this, "ddlDeployFn", {
      code: lambda.Code.fromAssetImage(deployDockerfile),
      handler: lambda.Handler.FROM_IMAGE,
      runtime: lambda.Runtime.FROM_IMAGE,
      timeout: cdk.Duration.minutes(3),
      architecture: dockerPlatform == "arm" ? lambda.Architecture.ARM_64 : lambda.Architecture.X86_64,
      vpc: props.vpc,
      securityGroups: [props.lambdaSG],
      vpcSubnets: privSubnets,
      environment:{
          "DB_NAME": dbName,
          "SQS_QUEUE_URL": ddlTriggerQueue.queueUrl,
          "DDL_SOURCE_BUCKET": sourceS3Bucket.bucketName
      },
    });
    // grant Connection property to the ddl init deploy function
    rdsInstance.grantConnect(ddlInitDeployFn);
    // create SQS event source
    const ddlEventSource = new SqsEventSource(ddlTriggerQueue);
    // trigger Lambda function upon message in SQS queue
    ddlInitDeployFn.addEventSource(ddlEventSource);
    // give S3 permissions
    sourceS3Bucket.grantRead(ddlInitDeployFn);
    // to be able to list secrets
    ddlInitDeployFn.role?.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName("SecretsManagerReadWrite")
    );

    // Docker assets for change lambda function
    const changeDockerfile = path.join(__dirname, "../lambda/rds-ddl-change/");

    // lambda function to deploy DDL on RDS (when there is a change to the DDL SQL File)
    const ddlChangeFn = new lambda.Function(this, "ddlChangeFn", {
      code: lambda.Code.fromAssetImage(changeDockerfile),
      handler: lambda.Handler.FROM_IMAGE,
      runtime: lambda.Runtime.FROM_IMAGE,
      timeout: cdk.Duration.minutes(10),
      architecture: dockerPlatform == "arm" ? lambda.Architecture.ARM_64 : lambda.Architecture.X86_64,
      vpc: props.vpc,
      securityGroups: [props.lambdaSG],
      vpcSubnets: privSubnets,
      environment:{
          "DB_NAME": dbName,
          "SQS_QUEUE_URL": ddlTriggerQueue.queueUrl,
          "DDL_SOURCE_BUCKET": sourceS3Bucket.bucketName
      },
    });
    // give S3 permissions
    sourceS3Bucket.grantRead(ddlChangeFn);
    // to be able to list secrets
    ddlChangeFn.role?.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName("SecretsManagerReadWrite")
    );
    // grant Connection property to the ddl init deploy function
    rdsInstance.grantConnect(ddlInitDeployFn);
    // to be able to describe cluster on RDS
    ddlChangeFn.role?.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName("AmazonRDSReadOnlyAccess")
    );

    const cfnChangesetRule = new events.Rule(this, 'cfnChangesetRule', {
          eventPattern: {
              "source": ["aws.cloudformation"],
              "detail": {
                "eventSource": ["cloudformation.amazonaws.com"],
                "eventName": ["ExecuteChangeSet"],
                "requestParameters": {
                  "stackName": [ddlSourceStackName]
                }
              }
          },
    });
    // Invoke the ddlChangeFn upon a matching event
    cfnChangesetRule.addTarget(new targets.LambdaFunction(ddlChangeFn));
  }
}
