history = np.delete(history, 0, axis=2)
print(history)
print(history.shape)
history = np.append(history, state, axis=2)
print(history)
print(history.shape)