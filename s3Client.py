# define an s3 class
class MyS3Client:
    def __init__(self, session, bucket):
        # save the bucket name
        self.bucket = bucket
        # create an s3 client
        self.s3 = session.client("s3")
    def obj_read(self, key):
        # retrieve the object
        response = self.s3.get_object(Bucket=self.bucket, Key=key)
        # return the decoded object
        return response["Body"].read().decode("utf-8")
    def obj_write(self, key, obj, contentType):
        # encode the object
        body = obj.encode("utf-8")
        # upload the object
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=contentType)
    def obj_lookup(self, key):
        # try to retrive head of key
        try:
            response = self.s3.head_object(Bucket=self.bucket, Key=key)
            return response
        except self.s3.exceptions.ClientError as e:
            # if it doesn't exist return None
            if e.response["Error"]["Code"] == "404":
                return None
            # otherwise, something else went wrong
            else:
                raise