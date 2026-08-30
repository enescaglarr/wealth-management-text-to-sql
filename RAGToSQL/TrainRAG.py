import json
from Helper.Database import get_connection
from Helper.SqlGuard import make_run_sql
from Helper.VannaObject import MyVanna
from Helper.Credentials import Credentials

import os
os.environ["TOKENIZERS_PARALLELISM"] = "false"



def get_ddls():
    file = open('TrainingRAG-Artifact/Proc.json', 'r')
    proc = json.load(file)
    file = open('TrainingRAG-Artifact/Tables.json', 'r')
    tables = json.load(file)
    file = open('TrainingRAG-Artifact/Views.json', 'r')
    views = json.load(file)
    data = []
    data.extend(tables)
    data.extend(views)
    data.extend(proc)
    return data


vn = MyVanna()

# This gives the package a function that it can use to run the SQL
conn = get_connection()

vn.run_sql = make_run_sql(conn)  # read-only guard: SELECT only, single statement, TOP injected
vn.run_sql_is_set = True
#
print("Connection Successfull")
# Training P1
df_information_schema = vn.run_sql("SELECT * FROM INFORMATION_SCHEMA.COLUMNS")
plan = vn.get_training_plan_generic(df_information_schema)

vn.train(plan=plan)
print("Plan trained Successfull")
# Training P2
for query in get_ddls():
    vn.train(ddl = query["command"])

print("DDL trained Successfull")
# Training P3
f = open("TrainingRAG-Artifact/Documentation.txt", "r")
documentation = f.read()

vn.train(documentation = documentation)
print("Documentation trained Successfull")

trained_data = vn.get_training_data()
trained_data.to_csv("training_summary.csv")



