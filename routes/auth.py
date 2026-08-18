from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from extensions import db, bcrypt
from models.user import User
from models.audit_log import AuditLog

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        user = User.query.filter_by(username=username).first()
        if user and bcrypt.check_password_hash(user.password_hash, password):
            if user.is_active:
                login_user(user)
                if user.role in ('ADMIN', 'SUPERVISOR'):
                    return redirect(url_for('admin.admin_home'))
                return redirect(url_for('leader.index'))
            else:
                flash('Usuário inativo. Contate o administrador.', 'danger')
        else:
            flash('Credenciais inválidas.', 'danger')

    return render_template('login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logout realizado com sucesso.', 'success')
    return redirect(url_for('auth.login'))


@auth_bp.route('/change-password', methods=['POST'])
@login_required
def change_password():
    """Let a logged-in user change their own password."""
    current_password = request.form.get('current_password', '')
    new_password = request.form.get('new_password', '')
    confirm_password = request.form.get('confirm_password', '')

    back = request.referrer or url_for('dashboard.index')

    if not bcrypt.check_password_hash(current_user.password_hash, current_password):
        flash('Senha atual incorreta.', 'danger')
        return redirect(back)

    if len(new_password) < 6:
        flash('A nova senha deve ter pelo menos 6 caracteres.', 'warning')
        return redirect(back)

    if new_password != confirm_password:
        flash('A confirmação da nova senha não confere.', 'warning')
        return redirect(back)

    current_user.password_hash = bcrypt.generate_password_hash(new_password).decode('utf-8')
    db.session.add(AuditLog(
        user_id=current_user.id,
        action='USER_PASSWORD_CHANGE',
        old_value=None,
        new_value=None
    ))
    db.session.commit()

    flash('Senha alterada com sucesso.', 'success')
    return redirect(back)