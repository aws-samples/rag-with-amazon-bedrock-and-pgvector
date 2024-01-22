import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as rds from 'aws-cdk-lib/aws-rds';
import { SqsEventSource } from 'aws-cdk-lib/aws-lambda-event-sources';
import path = require("path");


export interface PGVectorUpdateStackProps extends cdk.StackProps {
    vpc: ec2.Vpc;
    processedBucket: s3.Bucket;
    collectionName: string;
    apiKeySecret: secretsmanager.Secret;
    databaseCreds: string;
    triggerQueue: sqs.Queue;
    dbInstance: rds.DatabaseInstance;
    lambdaSG: ec2.SecurityGroup;
  }

export class PGVectorUpdateStack extends cdk.Stack {

  constructor(scope: Construct, id: string, props: PGVectorUpdateStackProps) {
    super(scope, id, props);

    // capturing architecture for docker container (arm or x86)
    const dockerPlatform = process.env["DOCKER_CONTAINER_PLATFORM_ARCH"]    
    
    // Docker assets for lambda function
    const dockerfilePGVectorUpdate = path.join(__dirname, "../lambda/pgvector-update/");
    
    // create a Lambda function to update the vector store everytime a new document is added to the processed bucket
    const pgvectorUpdateFn = new lambda.Function(this, "pgvectorUpdate", {
        code: lambda.Code.fromAssetImage(dockerfilePGVectorUpdate),
        handler: lambda.Handler.FROM_IMAGE,
        runtime: lambda.Runtime.FROM_IMAGE,
        vpc: props.vpc,
        securityGroups: [props.lambdaSG],
        vpcSubnets: props.vpc.selectSubnets({subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS}),
        timeout: cdk.Duration.minutes(3),
        memorySize: 512,
        architecture: dockerPlatform == "arm" ? lambda.Architecture.ARM_64 : lambda.Architecture.X86_64,
        environment: {
            "API_KEY_SECRET_NAME": props.apiKeySecret.secretName,
            "DB_CREDS": props.databaseCreds,
            "COLLECTION_NAME": props.collectionName,
            "QUEUE_URL": props.triggerQueue.queueUrl,
            // for under the hood stuff
            "NLTK_DATA": "/tmp"
        }
    });
    // grant lambda function permissions to read processed bucket
    props.processedBucket.grantRead(pgvectorUpdateFn);
    // grant lambda function permissions to ready the api key secret
    props.apiKeySecret.grantRead(pgvectorUpdateFn);
    // grant Connection permission to the function
    props.dbInstance.grantConnect(pgvectorUpdateFn);
    // create SQS event source
    const eventSource = new SqsEventSource(props.triggerQueue);
    // trigger Lambda function upon message in SQS queue
    pgvectorUpdateFn.addEventSource(eventSource);

    // for giving permissions to lambda to be able to extract database creds from Secrets Manager
    const smPolicyStatementDBCreds = new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
            "secretsmanager:GetResourcePolicy",
            "secretsmanager:GetSecretValue",
            "secretsmanager:DescribeSecret",
            "secretsmanager:ListSecretVersionIds"
            ],
        resources: [props.databaseCreds],
    });
    const smPolicyDBCreds = new iam.Policy(this, "dbCredsSecretsManagerPolicy", {
        statements : [smPolicyStatementDBCreds]
    });
    pgvectorUpdateFn.role?.attachInlinePolicy(smPolicyDBCreds);

  }
}
