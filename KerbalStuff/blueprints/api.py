from flask import Blueprint, render_template, abort, request, redirect, session, url_for
from flask.ext.login import current_user, login_user
from sqlalchemy import desc
from KerbalStuff.search import search_mods, search_users
from KerbalStuff.objects import *
from KerbalStuff.common import *
from KerbalStuff.config import _cfg
from KerbalStuff.email import send_update_notification, send_grant_notice

import os
import zipfile
import urllib

api = Blueprint('api', __name__)

default_description = """This is your mod listing! You can edit it as much as you like before you make it public.

To edit **this** text, you can click on the "**Edit this Mod**" button up there.

By the way, you have a lot of flexibility here. You can embed YouTube videos or screenshots. Be creative.

You can check out the Kerbal Stuff [markdown documentation](/markdown) for tips.

Thanks for hosting your mod on Kerbal Stuff!"""

#some helper functions to keep things consistant
def user_info(user):
    return {
        "username": user.username,
        "description": user.description,
        "forumUsername": user.forumUsername,
        "ircNick": user.ircNick,
        "twitterUsername": user.twitterUsername,
        "redditUsername": user.redditUsername
    }

def mod_info(mod):
    return {
        "name": mod.name,
        "id": mod.id,
        "short_description": mod.short_description,
        "downloads": mod.download_count,
        "followers": mod.follower_count,
        "author": mod.user.username,
        "default_version_id": mod.default_version().id
    }

def version_info(mod, version):
    return {
        "friendly_version": version.friendly_version,
        "ksp_version": version.ksp_version,
        "id": version.id,
        "download_path": url_for('mods.download', mod_id=mod.id,
                                 mod_name=mod.name,
                                 version=version.friendly_version),
        "changelog": version.changelog
    }

@api.route("/api/search/mod")
@json_output
def search_mod():
    query = request.args.get('query')
    page = request.args.get('page')
    query = '' if not query else query
    page = 1 if not page or not page.isdigit() else int(page)
    results = list()
    for m in search_mods(query, page, 30)[0]:
        a = mod_info(m)
        a['versions'] = list()
        for v in m.versions:
            a['versions'].append(version_info(m, v))
        results.append(a)
    return results

@api.route("/api/search/user")
@json_output
def search_user():
    query = request.args.get('query')
    page = request.args.get('page')
    query = '' if not query else query
    page = 0 if not page or not page.isdigit() else int(page)
    results = list()
    for u in search_users(query, page):
        a = user_info(u)
        a['mods'] = list()
        mods = Mod.query.filter(Mod.user == u, Mod.published == True).order_by(Mod.created)
        for m in mods:
            a['mods'].append(mod_info(m))
        results.append(a)
    return results

@api.route("/api/login", methods=['POST'])
@json_output
def login():
    username = request.form['username']
    password = request.form['password']
    if not username or not password:
        return { 'error': True, 'reason': 'Missing username or password' }, 400
    user = User.query.filter(User.username.ilike(username)).first()
    if not user:
        return { 'error': True, 'reason': 'Username or password is incorrect' }, 400
    if not bcrypt.checkpw(password, user.password):
        return { 'error': True, 'reason': 'Username or password is incorrect' }, 400
    if user.confirmation != '' and user.confirmation != None:
        return { 'error': True, 'reason': 'User is not confirmed' }, 400
    login_user(user)
    return { 'error': False }

@api.route("/api/mod/<modid>")
@json_output
def mod(modid):
    if not modid.isdigit():
        abort(400)
    mod = Mod.query.filter(Mod.id == modid).first()
    if not mod:
        abort(404)
    if not mod.published:
        abort(401)
    info = mod_info(mod)
    info["versions"] = list()
    for v in mod.versions:
        info["versions"].append(version_info(mod, v))
    return info

@api.route("/api/mod/<modid>/<version>")
@json_output
def mod_version(modid, version):
    if not modid.isdigit():
        abort(400)
    mod = Mod.query.filter(Mod.id == modid).first()
    if not mod:
        abort(404)
    if not mod.published:
        abort(401)
    if version == "latest" or version == "latest_version":
        v = mod.default_version()
    elif version.isdigit():
        v = ModVersion.query.filter(ModVersion.mod == mod,
                                    ModVersion.id == int(version)).first()
    else:
        abort(400)
    if not v:
        abort(404)
    info = version_info(mod, v)
    return info

@api.route("/api/user/<username>")
@json_output
def user(username):
    user = User.query.filter(User.username == username).first()
    if not user:
        abort(404)
    if not user.public:
        abort(401)
    mods = Mod.query.filter(Mod.user == user, Mod.published == True).order_by(
        Mod.created)
    info = user_info(user)
    info['mods'] = list()
    for m in mods:
        info['mods'].append(mod_info(m))
    return info

@api.route('/api/mod/<mod_id>/grant', methods=['POST'])
@with_session
@json_output
def grant_mod(mod_id):
    mod = Mod.query.filter(Mod.id == mod_id).first()
    if not mod:
        abort(404)
    editable = False
    if current_user:
        if current_user.admin:
            editable = True
        if current_user.id == mod.user_id:
            editable = True
    if not editable:
        abort(401)
    new_user = request.form.get('user')
    new_user = User.query.filter(User.username.ilike(new_user)).first()
    if new_user == None:
        return { 'error': True, 'message': 'The specified user does not exist.' }, 400
    if mod.user == new_user:
        return { 'error': True, 'message': 'This user has already been added.' }, 400
    if any(m.user == new_user for m in mod.shared_authors):
        return { 'error': True, 'message': 'This user has already been added.' }, 400
    if not new_user.public:
        return { 'error': True, 'message': 'This user has not made their profile public.' }, 400
    author = SharedAuthor()
    author.mod = mod
    author.user = new_user
    mod.shared_authors.append(author)
    db.add(author)
    db.commit()
    send_grant_notice(mod, new_user)
    return { 'error': False }, 200

@api.route('/api/mod/<mod_id>/accept_grant', methods=['POST'])
@with_session
@json_output
def accept_grant_mod(mod_id):
    if current_user == None:
        return { 'error': True, 'message': 'You are not logged in.' }, 401
    mod = Mod.query.filter(Mod.id == mod_id).first()
    if not mod:
        abort(404)
    author = [a for a in mod.shared_authors if a.user == current_user]
    if len(author) == 0:
        return { 'error': True, 'message': 'You do not have a pending authorship invite.' }, 200
    author = author[0]
    if author.accepted:
        return { 'error': True, 'message': 'You do not have a pending authorship invite.' }, 200
    author.accepted = True
    return { 'error': False }, 200

@api.route('/api/mod/<mod_id>/reject_grant', methods=['POST'])
@with_session
@json_output
def reject_grant_mod(mod_id):
    if current_user == None:
        return { 'error': True, 'message': 'You are not logged in.' }, 401
    mod = Mod.query.filter(Mod.id == mod_id).first()
    if not mod:
        abort(404)
    author = [a for a in mod.shared_authors if a.user == current_user]
    if len(author) == 0:
        return { 'error': True, 'message': 'You do not have a pending authorship invite.' }, 200
    author = author[0]
    if author.accepted:
        return { 'error': True, 'message': 'You do not have a pending authorship invite.' }, 200
    mod.shared_authors = [a for a in mod.shared_authors if a.user != current_user]
    db.delete(author)
    return { 'error': False }, 200

@api.route('/api/mod/<mod_id>/revoke', methods=['POST'])
@with_session
@json_output
def revoke_mod(mod_id):
    if current_user == None:
        return { 'error': True, 'message': 'You are not logged in.' }, 401
    mod = Mod.query.filter(Mod.id == mod_id).first()
    if not mod:
        abort(404)
    editable = False
    if current_user:
        if current_user.admin:
            editable = True
        if current_user.id == mod.user_id:
            editable = True
    if not editable:
        abort(401)
    new_user = request.form.get('user')
    new_user = User.query.filter(User.username.ilike(new_user)).first()
    if new_user == None:
        return { 'error': True, 'message': 'The specified user does not exist.' }, 400
    if mod.user == new_user:
        return { 'error': True, 'message': 'You can\'t remove yourself.' }, 400
    if not any(m.user == new_user for m in mod.shared_authors):
        return { 'error': True, 'message': 'This user is not an author.' }, 400
    author = [a for a in mod.shared_authors if a.user == new_user][0]
    mod.shared_authors = [a for a in mod.shared_authors if a.user != current_user]
    db.delete(author)
    return { 'error': False }, 200

@api.route('/api/mod/create', methods=['POST'])
@json_output
def create_mod():
    if not current_user:
        return { 'error': True, 'message': 'You are not logged in.' }, 401
    if not current_user.public:
        return { 'error': True, 'message': 'Only users with public profiles may create mods.' }, 403
    name = request.form.get('name')
    short_description = request.form.get('short-description')
    version = request.form.get('version')
    ksp_version = request.form.get('ksp-version')
    license = request.form.get('license')
    zipball = request.files.get('zipball')
    # Validate
    if not name \
        or not short_description \
        or not version \
        or not ksp_version \
        or not license \
        or not zipball:
        return { 'error': True, 'message': 'All fields are required.' }, 400
    # Validation, continued
    if len(name) > 100 \
        or len(short_description) > 1000 \
        or len(license) > 128:
        return { 'error': True, 'message': 'Fields exceed maximum permissible length.' }, 400
    mod = Mod()
    mod.user = current_user
    mod.name = name
    mod.short_description = short_description
    mod.description = default_description
    mod.license = license
    # Save zipball
    filename = secure_filename(name) + '-' + secure_filename(version) + '.zip'
    base_path = os.path.join(secure_filename(current_user.username) + '_' + str(current_user.id), secure_filename(name))
    full_path = os.path.join(_cfg('storage'), base_path)
    if not os.path.exists(full_path):
        os.makedirs(full_path)
    path = os.path.join(full_path, filename)
    if os.path.isfile(path):
        # We already have this version
        # We'll remove it because the only reason it could be here on creation is an error
        os.remove(path)
    zipball.save(path)
    if not zipfile.is_zipfile(path):
        os.remove(path)
        return { 'error': True, 'message': 'This is not a valid zip file.' }, 400
    version = ModVersion(secure_filename(version), ksp_version, os.path.join(base_path, filename))
    mod.versions.append(version)
    db.add(version)
    # Save database entry
    db.add(mod)
    db.commit()
    mod.default_version_id = version.id
    return { 'url': url_for("mods.mod", id=mod.id, mod_name=mod.name) }

@api.route('/api/mod/<mod_id>/update', methods=['POST'])
@with_session
@json_output
def update_mod(mod_id):
    if current_user == None:
        return { 'error': True, 'message': 'You are not logged in.' }, 401
    mod = Mod.query.filter(Mod.id == mod_id).first()
    if not mod:
        abort(404)
    editable = False
    if current_user:
        if current_user.admin:
            editable = True
        if current_user.id == mod.user_id:
            editable = True
        if any([u.accepted and u.user == current_user for u in mod.shared_authors]):
            editable = True
    if not editable:
        abort(401)
    version = request.form.get('version')
    changelog = request.form.get('changelog')
    ksp_version = request.form.get('ksp-version')
    notify = request.form.get('notify-followers')
    zipball = request.files.get('zipball')
    if not version \
        or not ksp_version \
        or not zipball:
        # Client side validation means that they're just being pricks if they
        # get here, so we don't need to show them a pretty error message
        abort(400)
    if notify == None:
        notify = False
    else:
        notify = notify.lower() == "true"
    filename = secure_filename(mod.name) + '-' + secure_filename(version) + '.zip'
    base_path = os.path.join(secure_filename(current_user.username) + '_' + str(current_user.id), secure_filename(mod.name))
    full_path = os.path.join(_cfg('storage'), base_path)
    if not os.path.exists(full_path):
        os.makedirs(full_path)
    path = os.path.join(full_path, filename)
    if os.path.isfile(path):
        return { 'error': True, 'message': 'We already have this version. Did you mistype the version number?' }, 400
    zipball.save(path)
    if not zipfile.is_zipfile(path):
        os.remove(path)
        return { 'error': True, 'message': 'This is not a valid zip file.' }, 400
    version = ModVersion(secure_filename(version), ksp_version, os.path.join(base_path, filename))
    version.changelog = changelog
    # Assign a sort index
    version.sort_index = max([v.sort_index for v in mod.versions]) + 1
    mod.versions.append(version)
    if notify:
        send_update_notification(mod, version, current_user)
    db.add(version)
    db.commit()
    mod.default_version_id = version.id
    return { 'url': url_for("mods.mod", id=mod.id, mod_name=mod.name) }
