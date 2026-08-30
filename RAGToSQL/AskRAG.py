from Helper.Database import get_connection
from Helper.SqlGuard import make_run_sql
from Helper.VannaObject import MyVanna
from Helper.Credentials import Credentials
import os

os.environ["TOKENIZERS_PARALLELISM"] = "false"


vn = MyVanna()

# This gives the package a function that it can use to run the SQL
conn = get_connection()
vn.run_sql = make_run_sql(conn)  # read-only guard: SELECT only, single statement, TOP injected
vn.run_sql_is_set = True

# print(vn.ask(question="Show me the total value of all portfolios managed for each client"))
print(vn.ask(question="Show the top 10 best-selling portfolios in terms of its value"))
