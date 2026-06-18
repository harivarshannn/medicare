from flask import Flask

def create_app():
    """Create and configure the Flask application.

    Returns:
        Flask: Configured Flask app instance.
    """
    app = Flask(__name__, static_folder="static", template_folder="templates")
    # Import and register blueprints
    from . import routes
    app.register_blueprint(routes.bp)
    return app
