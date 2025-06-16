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
    def add_entry(self, entry, table):
        logger.debug(f"Adding entry into table: {table}")
        # start a new connection
        conn = self.connect()
        # try to add a new user
        try:
            with conn.cursor() as cursor:
                # get the keys and values for the entry
                columns = ', '.join(entry.keys())
                placeholders = ', '.join(['%s']*len(entry))
                values = tuple(entry.values())
                sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
                cursor.execute(sql, values)
            conn.commit()
            logger.debug("Entry added successfully.")
        except Exception as e:
            logger.error(f"Failed to add entry': {e}", exc_info=True)
            raise
        # close the connection
        finally:
            self.disconnect(conn)
    def update_entry(self, updateValues, filters, table):
        logger.debug(f"Updating entry in table: {table}")
        # start a new connection
        conn = self.connect()
        # try to update a user
        try:
            with conn.cursor() as cursor:
                setColumns = ", ".join(f"{col} = %s" for col in updateValues.keys())
                filterColumns = " AND ".join(f"{col} = %s" for col in filters.keys())
                params = tuple(updateValues.values()) + tuple(filters.values())
                sql = f"UPDATE {table} SET {setColumns} WHERE {filterColumns}"
                cursor.execute(sql, params)
            conn.commit()
            logger.debug(f"Entry updated successfully.")
        except Exception as e:
            logger.error(f"Failed to update entry: {e}", exc_info=True)
            raise
        # close the connection
        finally:
            self.disconnect(conn)