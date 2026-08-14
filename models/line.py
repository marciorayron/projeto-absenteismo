from extensions import db


class Line(db.Model):
    __tablename__ = 'lines'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=False)
    project = db.Column(db.String(100), nullable=False)
    is_active = db.Column(db.Boolean, default=True)

    __table_args__ = (
        db.UniqueConstraint('name', 'project', name='uq_line_name_project'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'project': self.project,
            'is_active': self.is_active
        }

    def __repr__(self):
        return f'<Line {self.name} ({self.project})>'
