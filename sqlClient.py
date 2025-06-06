import pymysql
# define a MySql class
class MySQLClient:
    def __init__(self, host, user, password, db):
        # save MySQL database info
        self.host=host
        self.user=user
        self.password=password
        self.db=db
    def connect(self):
        # connect to the database
        conn = pymysql.connect(
            host=self.host,
            user=self.user,
            password=self.password,
            db=self.db,
            cursorclass=pymysql.cursors.DictCursor
        )
        return conn
    def disconnect(self, conn):
        # disconnect from the database
        conn.close()
    def add_entry(self, username, hashedPassword, email, table):
        # start a new connection
        conn = self.connect()
        # try to add a new user
        try:
            with conn.cursor() as cursor:
                sql = f"INSERT INTO {table} (username, password, email) VALUES (%s, %s, %s)"
                cursor.execute(sql, (username, hashedPassword, email))
            conn.commit()
            print(f"User '{username}' added successfully.")
        except Exception as e:
            print(f"[ERROR] Failed to add user: {e}")
        # close the connection
        finally:
            self.disconnect(conn)
    def update_entry(self, username, hashedPassword, table):
        # start a new connection
        conn = self.connect()
        # try to update a user
        try:
            with conn.cursor() as cursor:
                sql = f"UPDATE {table} SET password = %s WHERE username = %s"
                cursor.execute(sql, (hashedPassword, username))
            conn.commit()
            print(f"User '{username}' updated successfully.")
        except Exception as e:
            print(f"[ERROR] Failed to update user: {e}")
        # close the connection
        finally:
            self.disconnect(conn)
    def read_table(self, table):
        # start a new connection
        conn = self.connect()
        # try to lookup all users
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"SELECT * FROM {table}")
                results = cursor.fetchall()
                return results
        except Exception as e:
            print(f"[ERROR] Failed to read users: {e}")
            return []
        # close the connection
        finally:
            self.disconnect(conn)