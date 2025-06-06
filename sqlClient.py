import logging
logger = logging.getLogger(__name__)

import pymysql
# define a MySql class
class MySQLClient:
    def __init__(self, host, user, password, db):
        # save MySQL database info
        self.host=host
        self.user=user
        self.password=password
        self.db=db
        logger.debug(f"MySQLClient initialized for DB: {db} on host: {host}")
    def connect(self):
        logger.debug("Establishing MySQL database connection")
        # try to connect to the database
        try:
            conn = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                db=self.db,
                cursorclass=pymysql.cursors.DictCursor
            )
            logger.debug("MySQL connection established successfully")
            return conn
        except Exception as e:
            logger.error(f"Failed to connect to MySQL database: {e}", exc_info=True)
            raise
    def disconnect(self, conn):
        logger.debug("Closing MySQL database connection")
        # try to disconnect from the database
        try:
            conn.close()
            logger.debug("MySQL connection closed")
        except Exception as e:
            logger.error(f"Error closing MySQL connection: {e}", exc_info=True)
            raise
    def add_entry(self, username, hashedPassword, email, table):
        logger.debug(f"Adding entry for username: {username} into table: {table}")
        # start a new connection
        conn = self.connect()
        # try to add a new user
        try:
            with conn.cursor() as cursor:
                sql = f"INSERT INTO {table} (username, password, email) VALUES (%s, %s, %s)"
                cursor.execute(sql, (username, hashedPassword, email))
            conn.commit()
            print(f"User '{username}' added successfully.")
            logger.debug(f"User '{username}' added successfully.")
        except Exception as e:
            logger.error(f"Failed to add user '{username}': {e}", exc_info=True)
            raise
        # close the connection
        finally:
            self.disconnect(conn)
    def update_entry(self, username, hashedPassword, table):
        logger.debug(f"Updating password for username: {username} in table: {table}")
        # start a new connection
        conn = self.connect()
        # try to update a user
        try:
            with conn.cursor() as cursor:
                sql = f"UPDATE {table} SET password = %s WHERE username = %s"
                cursor.execute(sql, (hashedPassword, username))
            conn.commit()
            logger.debug(f"User '{username}' updated successfully.")
        except Exception as e:
            logger.error(f"Failed to update user '{username}': {e}", exc_info=True)
            raise
        # close the connection
        finally:
            self.disconnect(conn)
    def read_table(self, table):
        logger.debug(f"Reading all entries from table: {table}")
        # start a new connection
        conn = self.connect()
        # try to lookup all users
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"SELECT * FROM {table}")
                results = cursor.fetchall()
            logger.debug(f"Successfully read {len(results)} entries from table: {table}")
            return results
        except Exception as e:
            logger.error(f"Failed to read from table '{table}': {e}", exc_info=True)
            return []
        # close the connection
        finally:
            self.disconnect(conn)