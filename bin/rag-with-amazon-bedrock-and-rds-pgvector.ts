#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { RagAppStack } from '../lib/rag-app-stack';
import { BaseInfraStack } from '../lib/base-infra-stack';
import { RDSStack } from '../lib/rds-stack';
import { DDLSourceRDSStack } from '../lib/ddl-source-rds-stack';
import { RdsDdlAutomationStack } from '../lib/rds-ddl-automation-stack';
import { TestComputeStack } from '../lib/test-compute-stack';
import { PGVectorUpdateStack } from '../lib/pgvector-update-stack';


const app = new cdk.App();

// const deploymentEnv = { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION };

// contains vpc, 
const baseInfra = new BaseInfraStack(app, 'BaseInfraStack', {
});

// contains the RDS instance and its associated security group
const rds = new RDSStack(app, 'rdsStack', {
  vpc: baseInfra.vpc,
  sgLambda: baseInfra.lambdaSG,
  sgEc2: baseInfra.ec2SecGroup,
  ecsSecGroup: baseInfra.ecsTaskSecGroup,
});

// contains s3 bucket containing the RDS DDL file
const ddlSource = new DDLSourceRDSStack(app, 'ddlSourceStack', {
  rdsInstance: rds.dbInstance,
});

// contains s3 bucket containing the RDS DDL file
const ddlAutomation = new RdsDdlAutomationStack(app, 'ddlAutomationStack', {
  vpc: baseInfra.vpc,
  ddlTriggerQueue: baseInfra.rdsDdlTriggerQueue,
  dbName: rds.rdsDBName,
  ddlSourceS3Bucket: ddlSource.sourceS3Bucket,
  rdsInstance: rds.dbInstance,
  lambdaSG: baseInfra.lambdaSG,
  ddlSourceStackName: ddlSource.stackName,
});

// vector store update stack
const pgvectorUpdate = new PGVectorUpdateStack(app, 'PGVectorUpdateStack', {
  vpc: baseInfra.vpc,
  processedBucket: baseInfra.processedBucket,
  collectionName: baseInfra.pgvectorCollectionName,
  apiKeySecret: baseInfra.apiKeySecret,
  databaseCreds: rds.dbInstance.secret?.secretArn || "",
  triggerQueue: baseInfra.pgvectorQueue,
  dbInstance: rds.dbInstance,
  lambdaSG: baseInfra.lambdaSG,
});

// for a test EC2 instance to play around with (optional)
const testComputeStack = new TestComputeStack(app, 'TestComputeStack', {
  vpc: baseInfra.vpc,
  ec2SG: baseInfra.ec2SecGroup,
});

// ECS service running the RAG App
new RagAppStack(app, 'RagStack', {
  vpc: baseInfra.vpc,
  databaseCreds: rds.dbInstance.secret?.secretArn || "",
  collectionName: baseInfra.pgvectorCollectionName,
  apiKeySecret: baseInfra.apiKeySecret,
  dbInstance: rds.dbInstance,
  taskSecGroup: baseInfra.ecsTaskSecGroup,
  elbTargetGroup: baseInfra.appTargetGroup
});