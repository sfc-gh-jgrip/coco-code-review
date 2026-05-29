"""Small utility module with intentional defects for reviewer demo."""


def average(values):
    # Bug: divides by len(values) without guarding against an empty list,
    # raising ZeroDivisionError for average([]).
    total = 0
    for v in values:
        total += v
    return total / len(values)


def get_user_record(db, user_id):
    # Bug: SQL injection — user_id is interpolated directly into the query.
    query = "SELECT * FROM users WHERE id = '" + user_id + "'"
    return db.execute(query)
