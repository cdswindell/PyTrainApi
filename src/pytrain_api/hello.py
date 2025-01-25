# using flask_restful
from flask import Flask, jsonify, request
from flask_restful import Resource, Api, abort
from pytrain import CommandScope

from src.pytrain_api.pytrain_server import PyTrainServer

pytrain = PyTrainServer()

# creating the flask app
app = Flask(__name__)
# creating an API object
api = Api(app)


# making a class for a particular resource
# the get, post methods correspond to get and post requests
# they are automatically mapped by flask_restful.
# other methods include put, delete, etc.


class Hello(Resource):
    # corresponds to the GET request.
    # this function is called whenever there
    # is a GET request for this resource
    @staticmethod
    def get():
        return jsonify({"message": "hello world"})

        # Corresponds to POST request

    @staticmethod
    def post():
        data = request.get_json()  # status code
        return jsonify({"data": data}), 201


# another resource to calculate the square of a number
class Square(Resource):
    @staticmethod
    def get(num):
        return jsonify({"square": num**2})

class PyTrainComponent(Resource):
    def __init__(self, scope: CommandScope):
        super().__init__()
        self._scope = scope

    @property
    def scope(self) -> CommandScope:
        return self._scope

    def get(self, tmcc_id: int):
        state = pytrain.store.query(self.scope, tmcc_id)
        if state is None:
            abort(404, message=f"{self.scope.title} {tmcc_id} not found")
        else:
            return jsonify(state.as_dict())

class Engine(PyTrainComponent):
    def __init__(self):
        super().__init__(CommandScope.ENGINE)

class Train(PyTrainComponent):
    def __init__(self):
        super().__init__(CommandScope.TRAIN)

class Switch(PyTrainComponent):
    def __init__(self):
        super().__init__(CommandScope.SWITCH)

class Accessory(PyTrainComponent):
    def __init__(self):
        super().__init__(CommandScope.ACC)

class SensorTrack(PyTrainComponent):
    def __init__(self):
        super().__init__(CommandScope.IRDA)


# adding the defined resources along with their corresponding urls
api.add_resource(Hello, "/")
api.add_resource(Square, "/square/<int:tmcc_id>")
api.add_resource(Engine, "/engine/<int:tmcc_id>")
api.add_resource(Train, "/train/<int:tmcc_id>")
api.add_resource(Switch, "/switch/<int:tmcc_id>")
api.add_resource(Accessory, "/acc/<int:tmcc_id>")
api.add_resource(SensorTrack, "/sensor_track/<int:tmcc_id>")

# driver function
if __name__ == "__main__":
    app.run(debug=True)
