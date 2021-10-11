#!/usr/bin/env python

"""Creating chess puzzles for lichess.org"""

import argparse
import io
import logging
import os

import chess.engine
import chess.pgn

import pymongo

from modules.api.api import post_puzzle
from modules.bcolors.bcolors import bcolors
from modules.investigate.investigate import investigate
from modules.puzzle.puzzle import puzzle
from modules.utils.helpers import str2bool, get_stockfish_command, configure_logging, prepare_terminal


def prepare_settings():
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--max", metavar="MAX", nargs="?", type=int, default=20,
                        help="number of games to retrieve")
    parser.add_argument("--user", metavar="USER", nargs="?", type=str,
                        help="user to retrieve games")
    parser.add_argument("--threads", metavar="THREADS", nargs="?", type=int, default=4,
                        help="number of engine threads")
    parser.add_argument("--memory", metavar="MEMORY", nargs="?", type=int, default=2048,
                        help="memory in MB to use for engine hashtables")
    parser.add_argument("--depth", metavar="DEPTH", nargs="?", type=int, default=8,
                        help="depth for stockfish analysis")
    parser.add_argument("--quiet", dest="loglevel",
                        default=logging.DEBUG, action="store_const", const=logging.INFO,
                        help="substantially reduce the number of logged messages")
    parser.add_argument("--games", metavar="GAMES", default="games.pgn",
                        help="A specific pgn with games")
    parser.add_argument("--strict", metavar="STRICT", default=True,
                        help="If False then it will be generate more tactics but maybe a little ambiguous")
    parser.add_argument("--includeBlunder", metavar="INCLUDE_BLUNDER", default=True,
                        type=str2bool, const=True, dest="include_blunder", nargs="?",
                        help="If False then generated puzzles won't include initial blunder move")
    parser.add_argument("--stockfish", metavar="STOCKFISH", default=None, help="Path to Stockfish binary")

    return parser.parse_args()


settings = prepare_settings()

prepare_terminal()

configure_logging(settings.loglevel)

stockfish_command = get_stockfish_command(settings.stockfish)
logging.debug(f'Using {stockfish_command} to run Stockfish.')
engine = chess.engine.SimpleEngine.popen_uci(stockfish_command)
engine.configure({'Threads': settings.threads, 'Hash': settings.memory})

client = pymongo.MongoClient('url')
database = client["chesspecker-db"]
collection = database["users"]
userObject = collection.find_one({"id": settings.user})

def createSet() -> bool:
    newSet = {
        'user': userObject["_id"],
        'puzzles': [],
        'length': 0,
        'bestTime': 0,
    }
    try:
        collection = database["puzzlesets"]
        set_id = collection.insert_one(newSet).inserted_id
        return set_id
    except Exception as err:
        print(err)
        return False

def getSet() -> bool:
    try:
        collection = database["puzzlesets"]
        numberOfSets = collection.count_documents({"user": userObject["_id"]})
        if numberOfSets == 0:
            set_id = createSet()
        else:
            for current_set in collection.find({"user": userObject["_id"]}):
                if current_set["length"] < 30:
                    set_id = current_set["_id"]
                    break

        return set_id
    except Exception as err:
        print(err)
        return False

def updateGame(gameID: str) -> bool:
    try:
        setAsAnalyzed = { "$set" : { "analyzed": True }}
        collection = database["games"]
        collection.update_one({"game_id": gameID}, setAsAnalyzed)
        return True
    except Exception as err:
        print(err)
        return False

def insertPuzzle(puzzle) -> bool:
    try:
        collection = database["puzzles"]
        puzzle_id = collection.insert_one(puzzle).inserted_id
        set_id = getSet()
        collection = database["puzzlesets"]
        pushPuzzleToSet =  { "$push" : { "puzzles": puzzle_id }}
        collection.update_one({"_id": set_id}, pushPuzzleToSet)
        incrementSetLength = {'$inc': {"length": 1}}
        collection.update_one({"_id": set_id}, incrementSetLength)
        collection = database["puzzlesets"]
        incrementPuzzleNumber = {'$inc': {"puzzlesInDb": 1}}
        collection.update_one({"_id": userObject["_id"]}, incrementPuzzleNumber)
        return True
    except Exception as err:
        print(err)
        return False

try:
    collection = database["games"]
    for currentGame in collection.find({"user": userObject["_id"]}).limit(settings.max):
        if currentGame["analyzed"] == True:
            print(currentGame["game_id"])
            print("Already analyzed")
        else:
            print(currentGame["game_id"])
            print("Not analyzed yet")
            pgn = io.StringIO(currentGame["pgn"])
            game = chess.pgn.read_game(pgn)
            if game is None:
                break
            node = game

            game_id = currentGame["game_id"]
            logging.debug(bcolors.WARNING + "Game ID: " + str(game_id) + bcolors.ENDC)
            logging.debug(bcolors.WARNING + "Game headers: " + str(game) + bcolors.ENDC)

            prev_score = chess.engine.Cp(0)

            logging.debug(bcolors.OKGREEN + "Game Length: " + str(game.end().board().fullmove_number))
            logging.debug("Analysing Game..." + bcolors.ENDC)

            while not node.is_end():
                next_node = node.variation(0)
                info = engine.analyse(next_node.board(), chess.engine.Limit(depth=settings.depth))
                cur_score = info["score"].relative
                logging.debug(bcolors.OKGREEN + node.board().san(next_node.move) + bcolors.ENDC)
                logging.debug(bcolors.OKBLUE + "   CP: " + str(cur_score.score()) + bcolors.ENDC)
                logging.debug(bcolors.OKBLUE + "   Mate: " + str(cur_score.mate()) + bcolors.ENDC)

                if investigate(prev_score, cur_score, node.board()):
                    logging.debug(bcolors.WARNING + "   Investigate!" + bcolors.ENDC)
                    logging.debug(bcolors.WARNING + "Generating new puzzle..." + bcolors.ENDC)
                    currentPuzzle = puzzle(node.board(), next_node.move, str(game_id), engine, info, game, settings.strict)
                    currentPuzzle.generate(settings.depth)

                    if currentPuzzle.is_complete():
                        puzzle_pgn = post_puzzle(currentPuzzle, settings.include_blunder)
                        puzzle_json = currentPuzzle.to_json(settings.user, puzzle_pgn)
                        insertPuzzle(puzzle_json)

                prev_score = cur_score
                node = next_node
            updateGame(game_id)  
except Exception as err:
    print(err)
finally:
    print("all done")
    os._exit(1)