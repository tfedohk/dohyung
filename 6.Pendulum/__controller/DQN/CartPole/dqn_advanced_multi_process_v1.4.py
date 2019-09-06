'''

pickle file save & load TEST
content saved is
       "id":self.idx,
       "loss":loss,
       "l1_weights":l1_weights,
       "l2_weights":l2_weights

[process 0] save -> [process 1] load
vice versa
'''


# -*- coding: utf-8 -*-
# https://github.com/openai/gym/wiki/CartPole-v0

import tensorflow as tf
from tensorflow.keras.layers import Dense, Input
from tensorflow.keras.models import Model
from tensorflow.keras.optimizers import Adam
from collections import deque
import numpy as np
import random
import gym
from multiprocessing import Process
import pickle

print(tf.__version__)

ddqn = False
num_layers = 4
num_workers = 2


class DQNAgent():
    def __init__(self, async_dqn, idx, env_id, win_trials, win_reward, loss_trials):
        self.async_dqn = async_dqn

        self.idx = idx
        self.env = gym.make(env_id)
        self.env.seed(0)

        self.action_space = self.env.action_space

        # experience buffer
        self.memory = []

        # discount rate
        self.gamma = 0.9

        # initially 90% exploration, 10% exploitation
        self.epsilon = 1.0

        # iteratively applying decay til 10% exploration/90% exploitation
        self.epsilon_min = 0.1

        self.win_trials = win_trials
        self.win_reward = win_reward

        self.loss_trials = loss_trials

        # Q Network weights filename
        self.weights_file = './dqn_cartpole.h5'

        # Q Network for training
        self.n_inputs = self.env.observation_space.shape[0]
        self.n_outputs = self.action_space.n

        self.q_model = self.build_model(self.n_inputs, self.n_outputs)
        self.q_model.compile(loss='mse', optimizer=Adam())

        # target Q Network
        self.target_q_model = self.build_model(self.n_inputs, self.n_outputs)

        # copy Q Network params to target Q Network
        self.update_target_model_weights()

        self.replay_counter = 0

        if ddqn:
            print("----------Worker {0}: Double DQN--------".format(self.idx))
        else:
            print("-------------Worker {0}: DQN------------".format(self.idx))

        # print(self.q_model.get_layer(name="layer_1").get_config())
        # print(self.q_model.get_layer(name="layer_1").get_weights())

    # Q Network is 256-256-256-2 MLP
    def build_model(self, n_inputs, n_outputs):
        inputs = Input(shape=(n_inputs,), name='state_' + str(self.idx))
        x = Dense(256, activation='relu', name="layer_1_" + str(self.idx))(inputs)
        x = Dense(256, activation='relu', name="layer_2_" + str(self.idx))(x)
        x = Dense(256, activation='relu', name="layer_3_" + str(self.idx))(x)
        x = Dense(n_outputs, activation='linear', name='layer_4_' + str(self.idx))(x)
        model = Model(inputs, x)
        model.summary()
        return model

    # save Q Network params to a file
    def save_weights(self):
        self.q_model.save_weights(self.weights_file)

    def load_layers(self):
        l1 = self.q_model.get_layer(name="layer_" + str(1) + "_" + str(self.idx)).get_weights()
        l2 = self.q_model.get_layer(name="layer_" + str(2) + "_" + str(self.idx)).get_weights()

        return l1, l2

    # copy trained Q Network params to target Q Network
    def update_target_model_weights(self):
        self.target_q_model.set_weights(
            self.q_model.get_weights()
        )

    # copy trained other worker's Q Network params to current Q Network
    def update_layer_weights_from_other_worker(self, layer, weights):
        # pickle 로드

        self.q_model.get_layer(name="layer_" + str(layer)).set_weights(weights)

    # eps-greedy policy
    def act(self, state):
        if np.random.rand() < self.epsilon:
            # explore - do random action
            return self.action_space.sample()

        # exploit
        q_values = self.q_model.predict(state)

        # select the action with max Q-value
        return np.argmax(q_values[0])

    # store experiences in the replay buffer
    def remember(self, state, action, reward, next_state, done):
        item = (state, action, reward, next_state, done)
        self.memory.append(item)

    # compute Q_max
    # use of target Q Network solves the non-stationarity problem
    def get_target_q_value(self, next_state, reward):
        # max Q value among next state's actions
        if ddqn:
            # DDQN
            # current Q Network selects the action
            # a'_max = argmax_a' Q(s', a')
            action = np.argmax(self.q_model.predict(next_state)[0])

            # target Q Network evaluates the action
            # Q_max = Q_target(s', a'_max)
            q_max = self.target_q_model.predict(next_state)[0][action]
        else:
            # DQN chooses the max Q value among next actions
            # selection and evaluation of action is on the target Q Network
            # Q_max = max_a' Q_target(s', a')
            q_max = np.amax(self.target_q_model.predict(next_state)[0])

        # Q_value = reward + gamma * Q_max
        q_value = reward + self.gamma * q_max
        return q_value

    # experience replay addresses the correlation issue between samples
    def replay(self, batch_size, episode):
        # sars = state, action, reward, state' (next_state)
        sars_batch = random.sample(self.memory, batch_size)
        state_batch, q_values_batch = [], []

        # but easier to understand using a loop
        for state, action, reward, next_state, done in sars_batch:
            # policy prediction for a given state
            q_values = self.q_model.predict(state)

            # get Q_max
            q_value = self.get_target_q_value(next_state, reward)

            # correction on the Q value for the action used
            q_values[0][action] = reward if done else q_value

            # collect batch state-q_value mapping
            state_batch.append(state[0])
            q_values_batch.append(q_values[0])

        # train the Q-network
        self.q_model.fit(
            x=np.array(state_batch),
            y=np.array(q_values_batch),
            batch_size=batch_size,
            epochs=1,
            verbose=0
        )

        loss = self.q_model.evaluate(
            x=np.array(state_batch),
            y=np.array(q_values_batch),
            verbose=0
        )

        # update exploration-exploitation probability
        self.update_epsilon(episode)

        # copy new params on old target after every 10 training updates
        if self.replay_counter % 10 == 0:
            self.update_target_model_weights()

        self.replay_counter += 1

        return loss

    # decrease the exploration, increase exploitation
    def update_epsilon(self, episode):
        epsilon_decay = (self.epsilon_min / self.epsilon) ** (1. / float(episode))

        if self.epsilon > self.epsilon_min:
            self.epsilon *= epsilon_decay

    def start_rl(self):
        # should be solved in this number of episodes
        episode_count = 3000
        state_size = self.env.observation_space.shape[0]
        batch_size = 64

        # by default, CartPole-v0 has max episode steps = 200
        # you can use this to experiment beyond 200
        # env._max_episode_steps = 4000

        # Q-Learning sampling and fitting
        for episode in range(episode_count):
            state = self.env.reset()
            state = np.reshape(state, [1, state_size])
            done = False
            total_reward = 0
            while not done:
                # in CartPole-v0, action=0 is left and action=1 is right
                action = self.act(state)
                next_state, reward, done, _ = self.env.step(action)
                # in CartPole-v0:
                # state = [pos, vel, theta, angular speed]
                next_state = np.reshape(next_state, [1, state_size])
                # store every experience unit in replay buffer
                self.remember(state, action, reward, next_state, done)
                state = next_state
                total_reward += reward

            # call experience relay
            if len(self.memory) >= batch_size:
                loss = self.replay(batch_size, episode)
                # loss를 파일로 저장 및 load_layers() 호출하여 네트워크 파라미터들 저장

                mean_loss = self.async_dqn.update_loss(self.idx, loss)

                if episode % self.loss_trials == 0:
                    print("Worker {0} - Episode {1}: Mean Loss = {2}".format(
                        self.idx,
                        episode,
                        mean_loss
                    ))

                    l1_weights, l2_weights = self.load_layers()
                    content = {"id":self.idx,
                               "loss":loss,
                               "l1_weights":l1_weights,
                               "l2_weights":l2_weights
                               }
                    # pickle 저장(loss, l1, l2)
                    with open(str(self.idx)+'.txt', 'wb') as f:
                        # f.write(str(content))
                        pickle.dump(content, f)

                    if self.idx== 1:
                        idx = 0
                    else:
                        idx = 1

                    l1_weights, l2_weights = self.load_layers()
                    print("{0}th process's l1_weights is: {1}".format(
                        self.idx,
                        l1_weights
                    ))

                    try:
                        with open(str(idx)+'.txt', 'rb') as f:
                            data = pickle.load(f)
                            print("this process id is {0}, and the loaded data's id is {1}".format(
                                self.idx,
                                data['id']
                            ))
                            l1_weights = data['l1_weights']
                    except FileNotFoundError:
                        print("Not yet exist the file {0}".format(
                            str(idx) + '.txt'
                        ))

                    self.async_dqn.update_layer_weights(self.q_model, self.idx, l1_weights=l1_weights)

            mean_score = self.async_dqn.update_score(self.idx, total_reward)

            if episode % self.win_trials == 0:
                print(">>> Worker {0} - Episode {1}: Mean score = {2} in {3} episodes".format(
                    self.idx,
                    episode,
                    mean_score,
                    self.win_trials
                ))
                # loss 파일 로드 후, 자신의 Loss와 비교 및 update_layer_weights_from_other_worker() 호출

            if mean_score >= self.win_reward and episode >= self.win_trials:
                print("*** Worker {0} - Solved in episode {1}: Mean score = {2} in {3} episodes".format(
                    self.idx,
                    episode,
                    mean_score,
                    self.win_trials
                ))
                print("*** Worker {0} - Epsilon: {1}".format(self.idx, self.epsilon))
                self.save_weights()
                break

        # close the env and write monitor result info to disk
        self.env.close()


def process_func(async_dqn, idx, env_id, win_trials, win_reward, loss_trials):
    dqn_agent = DQNAgent(async_dqn, idx, env_id, win_trials, win_reward, loss_trials)
    dqn_agent.start_rl()


class AsyncDQN:
    def __init__(self):
        # discount rate
        self.gamma = 0.9

        # initially 90% exploration, 10% exploitation
        self.epsilon = 1.0

        # iteratively applying decay til 10% exploration/90% exploitation
        self.epsilon_min = 0.1

        # Q Network weights filename
        self.weights_file = './dqn_cartpole.h5'

        # the number of trials without falling over
        self.win_trials = 100

        # the CartPole-v0 is considered solved if for 100 consecutive trials,
        # the cart pole has not fallen over and it has achieved an average
        # reward of 195.0
        # a reward of +1 is provided for every timestep the pole remains
        # upright
        self.win_reward = 195.0

        # loss
        self.loss_trials = 10

        self.env_id = "CartPole-v0"

        # stores the reward per episode
        self.scores = {}
        self.losses = {}

        for idx in range(num_workers):
            self.scores[idx] = deque(maxlen=self.win_trials)
            self.losses[idx] = deque(maxlen=self.loss_trials)

    def train(self):
        workers = [
            Process(
                target=process_func,
                args=(
                    self,
                    idx,
                    self.env_id,
                    self.win_trials,
                    self.win_reward,
                    self.loss_trials
                )
            ) for idx in range(num_workers)
        ]

        for worker in workers:
            worker.start()

    def update_loss(self, worker_idx, loss):
        self.losses[worker_idx].append(loss)
        return np.mean(self.losses[worker_idx])

    def update_score(self, worker_idx, score):
        self.scores[worker_idx].append(score)
        return np.mean(self.scores[worker_idx])

    def update_layer_weights(self, q_model, id_, l1_weights):
        print("loaded data's l1_weights is: {0}".format(
            l1_weights
        ))
        q_model.get_layer(name="layer_" + str(1) + "_" + str(id_)).set_weights(l1_weights)
        print("set_weights() methods success")

if __name__ == '__main__':
    # instantiate the DQN/DDQN agent
    agent = AsyncDQN()
    agent.train()