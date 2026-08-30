"""Single place that opens a connection to the warehouse."""
import pyodbc

from .Credentials import Credentials


def get_connection():
    return pyodbc.connect(Credentials.connection_string())
