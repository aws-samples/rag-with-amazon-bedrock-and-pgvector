import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import path = require("path");
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as rds from 'aws-cdk-lib/aws-rds';

export interface DDLSourceRDSStackProps extends cdk.StackProps {
  rdsInstance: rds.DatabaseInstance;
}

export class DDLSourceRDSStack extends cdk.Stack {
  readonly sourceS3Bucket: s3.Bucket;

  constructor(scope: Construct, id: string, props: DDLSourceRDSStackProps) {
    super(scope, id, props);
    
    // create S3 bucket to host DDL file
    const ddlSourceBucket = new s3.Bucket(this, `ddlSourceBucket`, {
      bucketName: `ddl-source-${props.rdsInstance.instanceIdentifier}`
    });
    this.sourceS3Bucket = ddlSourceBucket;

    // create s3 bucket deployment to upload the DDL file
    new s3deploy.BucketDeployment(this, 'deployDDLSourceRDS', {
        sources: [s3deploy.Source.asset(path.join(__dirname, "../scripts/rds-ddl-sql"))],
        destinationBucket: ddlSourceBucket
    });
  }
}
