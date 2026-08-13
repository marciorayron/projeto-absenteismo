from flask import Flask, redirect, url_for, request, jsonify
from flask_login import login_required
from config import Config
from extensions import db, login_manager, bcrypt


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)
    bcrypt.init_app(app)

    login_manager.login_view = 'auth.login'

    @login_manager.user_loader
    def load_user(user_id):
        from models.user import User
        return db.session.get(User, int(user_id))

    @app.route('/')
    def index():
        return redirect(url_for('auth.login'))

    @app.route('/api/cascade-options')
    @login_required
    def api_cascade_options():
        """Shared cascade endpoint: returns distinct shifts, projects, lines filtered by parent selections."""
        from models.allocation import Allocation

        selected_shifts = request.args.getlist('shifts')
        selected_projects = request.args.getlist('projects')

        # Shifts
        shifts_query = db.session.query(Allocation.shift).filter(Allocation.end_date.is_(None))
        if selected_shifts:
            try:
                shifts_query = shifts_query.filter(Allocation.shift.in_([int(s) for s in selected_shifts if s]))
            except ValueError:
                pass
        shifts_rows = shifts_query.distinct().order_by(Allocation.shift).all()
        shifts = [s[0] for s in shifts_rows if s[0] is not None]

        # Projects — filtered by selected shifts
        projects_query = db.session.query(Allocation.project).filter(Allocation.end_date.is_(None))
        if selected_shifts:
            try:
                projects_query = projects_query.filter(Allocation.shift.in_([int(s) for s in selected_shifts if s]))
            except ValueError:
                pass
        projects_rows = projects_query.distinct().order_by(Allocation.project).all()
        projects = sorted([p[0] for p in projects_rows if p[0]])

        # Lines — filtered by selected shifts AND projects
        lines_query = db.session.query(Allocation.line).filter(Allocation.end_date.is_(None))
        if selected_shifts:
            try:
                lines_query = lines_query.filter(Allocation.shift.in_([int(s) for s in selected_shifts if s]))
            except ValueError:
                pass
        if selected_projects:
            lines_query = lines_query.filter(Allocation.project.in_(selected_projects))
        lines_rows = lines_query.distinct().order_by(Allocation.line).all()
        lines = sorted([l[0] for l in lines_rows if l[0]])

        return {'shifts': shifts, 'projects': projects, 'lines': lines}

    from routes.auth import auth_bp
    from routes.leader import leader_bp
    from routes.admin import admin_bp
    from routes.dashboard import dashboard_bp
    from routes.reports import reports_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(leader_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(reports_bp)

    with app.app_context():
        db.create_all()
        from migrate import ensure_schema
        ensure_schema()
        from seed import create_initial_users
        create_initial_users(app)

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, host='0.0.0.0', port=5000)