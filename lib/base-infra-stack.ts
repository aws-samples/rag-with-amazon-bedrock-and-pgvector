import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as s3 from "aws-cdk-lib/aws-s3";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3notif from 'aws-cdk-lib/aws-s3-notifications';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as elbv2 from "aws-cdk-lib/aws-elasticloadbalancingv2";
import * as elbv2_actions from "aws-cdk-lib/aws-elasticloadbalancingv2-actions";
import { SqsEventSource } from 'aws-cdk-lib/aws-lambda-event-sources';
  import * as cloudtrail from "aws-cdk-lib/aws-cloudtrail";

import path = require("path");

export class BaseInfraStack extends cdk.Stack {
  readonly vpc: ec2.Vpc;
  readonly lambdaSG: ec2.SecurityGroup;
  readonly ecsTaskSecGroup: ec2.SecurityGroup;
  readonly knowledgeBaseBucket: s3.Bucket;
  readonly processedBucket: s3.Bucket;
  readonly rdsDdlTriggerQueue: sqs.Queue;
  readonly pgvectorQueue: sqs.Queue;
  readonly pgvectorCollectionName: string;
  readonly apiKeySecret: secretsmanager.Secret;
  readonly appTargetGroup: elbv2.ApplicationTargetGroup;
  readonly ec2SecGroup: ec2.SecurityGroup;


  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    /* 
    capturing region env var to know which region to deploy this infrastructure

    NOTE - the AWS profile that is used to deploy should have the same default region
    */
    let validRegions: string[] = ['us-east-1', 'us-west-2', 'eu-west-2'];
    const regionPrefix = process.env.CDK_DEFAULT_REGION || 'us-east-1';
    console.log(`CDK_DEFAULT_REGION: ${regionPrefix}`);
   // throw error if unsupported CDK_DEFAULT_REGION specified
    if (!(validRegions.includes(regionPrefix))) {
        throw new Error('Unsupported CDK_DEFAULT_REGION specified')
    };

    // Trail for logging AWS API events
    const trail = new cloudtrail.Trail(this, 'myCloudTrail', {
      managementEvents: cloudtrail.ReadWriteType.ALL
    });

  // collection name used by the vector store (used to update and retrieve content)
    this.pgvectorCollectionName = `pgvector-collection-${regionPrefix}-${this.account}`

    // create VPC to deploy the infrastructure in
    const vpc = new ec2.Vpc(this, "InfraNetwork", {
      ipAddresses: ec2.IpAddresses.cidr('10.80.0.0/20'),
      availabilityZones: [`${regionPrefix}a`, `${regionPrefix}b`, `${regionPrefix}c`],
      subnetConfiguration: [
          {
            name: "public",
            subnetType: ec2.SubnetType.PUBLIC,
          },
          {
            name: "private",
            subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
          }
      ],
    });
    this.vpc = vpc;

    // create bucket for knowledgeBase
    const docsBucket = new s3.Bucket(this, `knowledgeBase`, {});
    this.knowledgeBaseBucket = docsBucket;
    // use s3 bucket deploy to upload documents from local repo to the knowledgebase bucket
    new s3deploy.BucketDeployment(this, 'knowledgeBaseBucketDeploy', {
        sources: [s3deploy.Source.asset(path.join(__dirname, "../knowledgebase"))],
        destinationBucket: docsBucket
    });

    // create bucket for processed text (from PDF to txt)
    const processedTextBucket = new s3.Bucket(this, `processedText`, {});
    this.processedBucket = processedTextBucket;

    // capturing architecture for docker container (arm or x86)
    const dockerPlatform = process.env["DOCKER_CONTAINER_PLATFORM_ARCH"]

    // Docker assets for lambda function
    const dockerfile = path.join(__dirname, "../lambda/pdf-processor/");
    // create a Lambda function to process knowledgebase pdf documents
    const lambdaFn = new lambda.Function(this, "pdfProcessorFn", {
        code: lambda.Code.fromAssetImage(dockerfile),
        handler: lambda.Handler.FROM_IMAGE,
        runtime: lambda.Runtime.FROM_IMAGE,
        timeout: cdk.Duration.minutes(15),
        memorySize: 512,
        architecture: dockerPlatform == "arm" ? lambda.Architecture.ARM_64 : lambda.Architecture.X86_64,
        environment: {
            "SOURCE_BUCKET_NAME": docsBucket.bucketName,
            "DESTINATION_BUCKET_NAME": processedTextBucket.bucketName
        }
    });
    // grant lambda function permissions to read knowledgebase bucket
    docsBucket.grantRead(lambdaFn);
    // grant lambda function permissions to write to the processed text bucket
    processedTextBucket.grantWrite(lambdaFn);

    // create a new S3 notification that triggers the pdf processor lambda function
    const kbNotification = new s3notif.LambdaDestination(lambdaFn);
    // assign notification for the s3 event type
    docsBucket.addEventNotification(s3.EventType.OBJECT_CREATED, kbNotification);
    
    // Queue for triggering initialization (DDL deployment) of RDS 
    const rdsDdlDetectionQueue = new sqs.Queue(this, 'rdsDdlDetectionQueue', {
      queueName: "RDS_DDL_Detection_Queue",
      visibilityTimeout: cdk.Duration.minutes(6)
    });
    this.rdsDdlTriggerQueue = rdsDdlDetectionQueue;

    // Function that gets triggered on the creation of an RDS cluster
    const rdsDdlTriggerFn = new lambda.Function(this, "rdsDdlTriggerFn", {
        code: lambda.Code.fromAsset(path.join(__dirname, "../lambda/rds-ddl-trigger")),
        runtime: lambda.Runtime.PYTHON_3_11,
        timeout: cdk.Duration.minutes(2),
        handler: "app.lambda_handler",
        environment:{
          "RDS_DDL_QUEUE_URL": rdsDdlDetectionQueue.queueUrl,
      },
    });
    // give permission to the function to be able to send messages to the queues
    rdsDdlDetectionQueue.grantSendMessages(rdsDdlTriggerFn);

    // Trigger an event when there is a RDS CreateDB API call recorded in CloudTrail
    const eventBridgeCreateDBRule = new events.Rule(this, 'eventBridgeCreateDBRule', {
        eventPattern: {
            source: ["aws.rds"],
            detail: {
            eventSource: ["rds.amazonaws.com"],
            eventName: ["CreateDBInstance"]
            }
        },
    });
    // Invoke the rdsDdlTriggerFn upon a matching event
    eventBridgeCreateDBRule.addTarget(new targets.LambdaFunction(rdsDdlTriggerFn));

    // Create security group for Lambda functions interacting with RDS (not defined in this stack)
    const lambdaSecGroupName = "lambda-security-group";
    const lambdaSecurityGroup = new ec2.SecurityGroup(this, lambdaSecGroupName, {
        securityGroupName: lambdaSecGroupName,
        vpc: vpc,
        // for internet access
        allowAllOutbound: true
    });
    this.lambdaSG = lambdaSecurityGroup;

    // Create security group for test ec2 instance (will be removed later)
    const ec2SecGroupName = "ec2-security-group";
    const ec2SecurityGroup = new ec2.SecurityGroup(this, ec2SecGroupName, {
        securityGroupName: ec2SecGroupName,
        vpc: vpc,
        // for internet access
        allowAllOutbound: true
    });
    this.ec2SecGroup = ec2SecurityGroup;

    // to store the API KEY for OpenAI embeddings
    const oaiSecret = 'openAiApiKey';
    const openAiApiKey = new secretsmanager.Secret(this, oaiSecret, {
      secretName: oaiSecret
    });
    this.apiKeySecret = openAiApiKey;

    // Queue for triggering pgvector update
    const pgVectorUpdateQueue = new sqs.Queue(this, 'pgVectorUpdateQueue', {
      queueName: "PGVector_Update_Queue",
      visibilityTimeout: cdk.Duration.minutes(5)
    });
    this.pgvectorQueue = pgVectorUpdateQueue;
     
    // create a Lambda function to send message to SQS for vector store updates
    const pgvectorTriggerFn = new lambda.Function(this, "pgvectorTrigger", {
         code: lambda.Code.fromAsset(path.join(__dirname, "../lambda/pgvector-trigger")),
         runtime: lambda.Runtime.PYTHON_3_11,
         handler: "app.lambda_handler",
         timeout: cdk.Duration.minutes(2),
         environment: {
          "PGVECTOR_UPDATE_QUEUE": pgVectorUpdateQueue.queueUrl,
          "BUCKET_NAME": processedTextBucket.bucketName
         }
     });
    // create a new S3 notification that triggers the pgvector trigger lambda function
    const processedBucketNotif = new s3notif.LambdaDestination(pgvectorTriggerFn);
    // assign notification for the s3 event type
    processedTextBucket.addEventNotification(s3.EventType.OBJECT_CREATED, processedBucketNotif);
    // give permission to the function to be able to send messages to the queues
    pgVectorUpdateQueue.grantSendMessages(pgvectorTriggerFn);

    // Security group for ECS tasks
    const ragAppSecGroup = new ec2.SecurityGroup(this, "ragAppSecGroup", {
        securityGroupName: "ecs-rag-sec-group",
        vpc: vpc,
        allowAllOutbound: true,
    });
    this.ecsTaskSecGroup = ragAppSecGroup;

    // Security group for ALB
    const albSecGroup = new ec2.SecurityGroup(this, "albSecGroup", {
          securityGroupName: "alb-sec-group",
          vpc: vpc,
          allowAllOutbound: true,
    });

    // create load balancer
    const appLoadBalancer = new elbv2.ApplicationLoadBalancer(this, 'ragAppLb', {
      vpc: vpc,
      internetFacing: true,
      securityGroup: albSecGroup
    });

    const certName = process.env.IAM_SELF_SIGNED_SERVER_CERT_NAME;
    // throw error if IAM_SELF_SIGNED_SERVER_CERT_NAME is undefined
    if (certName === undefined || certName === '') {
        throw new Error('Please specify the "IAM_SELF_SIGNED_SERVER_CERT_NAME" env var')
    };
    console.log(`self signed cert name: ${certName}`);

    const cognitoDomain = process.env.COGNITO_DOMAIN_NAME;
    // throw error if COGNITO_DOMAIN_NAME is undefined
    if (cognitoDomain === undefined || cognitoDomain === '') {
        throw new Error('Please specify the "COGNITO_DOMAIN_NAME" env var')
    };
    console.log(`cognito domain name: ${cognitoDomain}`);

    // create Target group for ECS service
    const ecsTargetGroup = new elbv2.ApplicationTargetGroup(this, 'default', {
      vpc: vpc,
      protocol: elbv2.ApplicationProtocol.HTTP,
      port: 8501
    });
    this.appTargetGroup = ecsTargetGroup;

    // Queue for triggering app client creation
    const appClientCreationQueue = new sqs.Queue(this, 'appClientCreateQueue', {
      queueName: "COG_APP_CLIENT_CREATE_QUEUE",
      visibilityTimeout: cdk.Duration.minutes(5)
    });

    // create a Lambda function to send message to SQS for vector store updates
    const appClientCreateTriggerFn = new lambda.Function(this, "appClientCreateTrigger", {
        code: lambda.Code.fromAsset(path.join(__dirname, "../lambda/app-client-create-trigger")),
        runtime: lambda.Runtime.PYTHON_3_11,
        handler: "app.lambda_handler",
        timeout: cdk.Duration.minutes(2),
        environment: {
          "TRIGGER_QUEUE": appClientCreationQueue.queueUrl,
        }
      });
    // give permission to the function to be able to send messages to the queues
    appClientCreationQueue.grantSendMessages(appClientCreateTriggerFn);

    // Trigger an event when there is a Cognito CreateUserPoolClient call recorded in CloudTrail
    const appClientCreateRule = new events.Rule(this, 'appClientCreateRule', {
        eventPattern: {
            source: ["aws.cognito-idp"],
            detail: {
            eventSource: ["cognito-idp.amazonaws.com"],
            eventName: ["CreateUserPoolClient"],
            sourceIPAddress: ["cloudformation.amazonaws.com"]
            }
        },
    });
    appClientCreateRule.node.addDependency(appClientCreationQueue);
    // Invoke the callBack update fn upon a matching event
    appClientCreateRule.addTarget(new targets.LambdaFunction(appClientCreateTriggerFn));

    // create cognito user pool
    const userPool = new cognito.UserPool(this, "UserPool", {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      selfSignUpEnabled: true,
      signInAliases: { email: true},
      autoVerify: { email: true }
    });
    userPool.node.addDependency(appClientCreateRule);

    // create cognito user pool domain
    const userPoolDomain = new cognito.UserPoolDomain(this, 'upDomain', {
      userPool,
      cognitoDomain: {
        domainPrefix: cognitoDomain
      }
    });

    // create and add Application Integration for the User Pool
    const client = userPool.addClient("WebClient", {
      userPoolClientName: "MyAppWebClient",
      idTokenValidity: cdk.Duration.days(1),
      accessTokenValidity: cdk.Duration.days(1),
      generateSecret: true,
      authFlows: {
        adminUserPassword: true,
        userPassword: true,
        userSrp: true
      },
      oAuth: {
        flows: {authorizationCodeGrant: true},
        scopes: [cognito.OAuthScope.OPENID],
        callbackUrls: [ `https://${appLoadBalancer.loadBalancerDnsName}/oauth2/idpresponse` ]
      },
      supportedIdentityProviders: [cognito.UserPoolClientIdentityProvider.COGNITO]
    });
    client.node.addDependency(appClientCreateRule);

    // add https listener to the load balancer
    const httpsListener = appLoadBalancer.addListener("httpsListener", {
      port: 443,
      open: true,
      certificates: [
        {
          certificateArn: `arn:aws:iam::${this.account}:server-certificate/${certName}`
        },
      ],
      defaultAction: new elbv2_actions.AuthenticateCognitoAction({
        userPool: userPool,
        userPoolClient: client,
        userPoolDomain: userPoolDomain,
        next: elbv2.ListenerAction.forward([ecsTargetGroup])
      })
    });
    /* 
    
    create lambda function because ALB dns name is not lowercase, 
    and cognito does not function as intended due to that
    
    Reference - https://github.com/aws/aws-cdk/issues/11171

    */
    const callBackInitFn = new lambda.Function(this, "callBackInit", {
      code: lambda.Code.fromAsset(path.join(__dirname, "../lambda/call-back-url-init")),
      runtime: lambda.Runtime.PYTHON_3_11,
      timeout: cdk.Duration.minutes(2),
      handler: "app.lambda_handler",
      environment:{
        "USER_POOL_ID": userPool.userPoolId,
        "APP_CLIENT_ID": client.userPoolClientId,
        "ALB_DNS_NAME": appLoadBalancer.loadBalancerDnsName,
        "SQS_QUEUE_URL": appClientCreationQueue.queueUrl,
      },
    });
    callBackInitFn.role?.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName("AmazonCognitoPowerUser")
    );
    // create SQS event source
    const appClientCreateSqsEventSource = new SqsEventSource(appClientCreationQueue);
    // trigger Lambda function upon message in SQS queue
    callBackInitFn.addEventSource(appClientCreateSqsEventSource);

    const callBackUpdateFn = new lambda.Function(this, "callBackUpdate", {
      code: lambda.Code.fromAsset(path.join(__dirname, "../lambda/call-back-url-update")),
      runtime: lambda.Runtime.PYTHON_3_11,
      timeout: cdk.Duration.minutes(2),
      handler: "app.lambda_handler",
      environment:{
        "USER_POOL_ID": userPool.userPoolId,
        "APP_CLIENT_ID": client.userPoolClientId,
        "ALB_DNS_NAME": appLoadBalancer.loadBalancerDnsName
      },
    });
    callBackUpdateFn.role?.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName("AmazonCognitoPowerUser")
    );

    // Trigger an event when there is a Cognito CreateUserPoolClient call recorded in CloudTrail
    const appClientUpdateRule = new events.Rule(this, 'appClientUpdateRule', {
        eventPattern: {
            source: ["aws.cognito-idp"],
            detail: {
            eventSource: ["cognito-idp.amazonaws.com"],
            eventName: ["UpdateUserPoolClient"],
            sourceIPAddress: ["cloudformation.amazonaws.com"]
            }
        },
    });
    // Invoke the callBack update fn upon a matching event
    appClientUpdateRule.addTarget(new targets.LambdaFunction(callBackUpdateFn)); 
  }
}
