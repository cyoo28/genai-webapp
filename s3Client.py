import logging
logger = logging.getLogger(__name__)

# define an s3 class
class MyS3Client:
    def __init__(self, session, bucket):
        # save the bucket name
        self.bucket = bucket
        # create an s3 client
        self.s3 = session.client("s3")
        logger.debug(f"MyS3Client initialized for bucket: {bucket}")
    def obj_read(self, key):
        logger.debug(f"Attempting to read S3 object with key: {key}")
        # try to retrieve the object
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=key)
            data = response["Body"].read().decode("utf-8")
            logger.debug(f"Successfully read S3 object with key: {key}")
            # return the decoded object
            return data
        except Exception as e:
            logger.error(f"Error reading S3 object with key {key}: {e}", exc_info=True)
            raise
    def obj_write(self, key, obj, contentType):
        logger.debug(f"Attempting to write S3 object with key: {key}")
        # try to upload the object
        try:
            body = obj.encode("utf-8")
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=contentType)
            logger.debug(f"Successfully wrote S3 object with key: {key}")
        except Exception as e:
            logger.error(f"Error writing S3 object with key {key}: {e}", exc_info=True)
            raise
    def obj_lookup(self, key):
        logger.debug(f"Attempting to lookup S3 object with key: {key}")
        # try to retrive head of key
        try:
            response = self.s3.head_object(Bucket=self.bucket, Key=key)
            logger.debug(f"Object found for key: {key}")
            return response
        except self.s3.exceptions.ClientError as e:
            # if it doesn't exist return None
            if e.response["Error"]["Code"] == "404":
                logger.debug(f"Object with key {key} not found")
                return None
            # otherwise, something else went wrong
            else:
                logger.error(f"ClientError looking up object with key {key}: {e}", exc_info=True)
                raise
        except Exception as e:
            logger.error(f"Unexpected error looking up object with key {key}: {e}", exc_info=True)
            raise
